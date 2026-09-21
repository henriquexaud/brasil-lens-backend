"""Contrato real INPE, recorte exato, cache/falhas e consulta pontual."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.core.config import settings
from app.core.errors import InvalidParameterError, ProviderError, TerritoryNotFoundError
from app.models import TerritoryLevel
from app.services import fire_hotspots as service


def payload() -> dict:
    # Campos confirmados no DescribeFeatureType e GetFeature oficial do BDQueimadas.
    return {
        "type": "FeatureCollection",
        "numberMatched": 77157,
        "features": [
            {
                "type": "Feature",
                "id": "focos.fid-temporario",
                "geometry": {"type": "Point", "coordinates": [-49.2089, -2.4636]},
                "properties": {
                    "id_foco_bdq": 1891867420,
                    "data_hora_gmt": "2026-09-20T00:00:00Z",
                    "satelite": "GOES-19",
                    "municipio": "MOCAJUBA",
                    "estado": "PARÁ",
                    "id_1": 15,
                    "id_2": 1504604,
                    "bioma": "Amazônia",
                    "precipitacao": 0,
                    "numero_dias_sem_chuva": 12,
                    "risco_fogo": 0.8,
                    "frp": 72.6,
                },
            }
        ],
    }


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    service._cache.clear()
    service._fallback.clear()
    service._failures.clear()
    service._locks.clear()


@respx.mock
async def test_real_contract_and_complete_coverage_metadata() -> None:
    upstream = respx.get(settings.inpe_queimadas_wfs_url).mock(
        return_value=httpx.Response(200, json=payload())
    )
    result = await service.get_fire_hotspots(AsyncMock())
    assert result.metadata.hotspot_count == 77157
    assert len(result.features) == 1  # Prévia apenas; sem truncar a cobertura WMS.
    assert result.metadata.wms_layer == "bdqueimadas:focos"
    params = upstream.calls[0].request.url.params
    assert params["typeNames"] == "bdqueimadas:focos"
    assert params["count"] == "1"
    assert "id_0=33 AND data_hora_gmt >=" in params["cql_filter"]
    assert params["cql_filter"] == result.metadata.cql_filter
    feature = result.features[0]
    assert feature.id == "inpe:1891867420"  # ID persistente, não o fid temporário WFS.
    assert feature.properties.municipality_code == "1504604"
    assert feature.properties.detected_at.utcoffset() == timedelta(0)
    assert feature.properties.fire_risk == 0.8
    assert feature.properties.precipitation_mm == 0
    assert feature.properties.frp == 72.6
    again = await service.get_fire_hotspots(AsyncMock())
    assert again == result
    assert upstream.call_count == 1


@pytest.mark.parametrize("value", [None, -999, "NaN", "Infinity", "", True])
def test_missing_measurements_are_not_zero_or_false_risk(value: object) -> None:
    raw = payload()["features"][0]
    raw["properties"].update(
        risco_fogo=value, frp=value, precipitacao=value, numero_dias_sem_chuva=value
    )
    properties = service._parse_feature(raw).properties
    assert (
        properties.fire_risk
        is properties.frp
        is properties.precipitation_mm
        is properties.days_without_rain
        is None
    )


@pytest.mark.parametrize(
    "level,code,field", [("state", "15", "id_1"), ("municipality", "1504604", "id_2")]
)
async def test_scope_uses_ibge_codes_not_just_bbox(
    monkeypatch: pytest.MonkeyPatch, level: str, code: str, field: str
) -> None:
    monkeypatch.setattr(
        service.territories,
        "get_by_code",
        AsyncMock(return_value=SimpleNamespace(level=TerritoryLevel(level))),
    )
    fetch = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(service, "_fetch_wfs", fetch)
    result = await service.get_fire_hotspots(AsyncMock(), level=level, parent_code=code)
    assert result.metadata.parent_code == code
    assert f"id_0=33 AND {field}={code} AND" in fetch.call_args.args[0]


async def test_invalid_scope_does_not_silently_fall_back_to_brazil(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for level, code in [
        ("state", None),
        ("municipality", "15"),
        ("country", "15"),
        ("state", "1'"),
    ]:
        with pytest.raises(InvalidParameterError):
            await service.get_fire_hotspots(AsyncMock(), level=level, parent_code=code)
    monkeypatch.setattr(service.territories, "get_by_code", AsyncMock(return_value=None))
    with pytest.raises(TerritoryNotFoundError):
        await service.get_fire_hotspots(AsyncMock(), level="state", parent_code="99")


@respx.mock
async def test_empty_is_success_but_server_error_is_not_an_empty_result() -> None:
    upstream = respx.get(settings.inpe_queimadas_wfs_url).mock(
        return_value=httpx.Response(
            200, json={"type": "FeatureCollection", "numberMatched": 0, "features": []}
        )
    )
    empty = await service.get_fire_hotspots(AsyncMock())
    assert empty.metadata.hotspot_count == 0
    assert empty.metadata.latest_detection_at is None
    service._cache.clear()
    service._fallback.clear()
    upstream.mock(return_value=httpx.Response(503))
    with pytest.raises(ProviderError):
        await service.get_fire_hotspots(AsyncMock())
    with pytest.raises(ProviderError):
        await service.get_fire_hotspots(AsyncMock())
    assert upstream.call_count == 2  # Cooldown após a primeira falha.


@respx.mock
async def test_fallback_preserves_timestamp_and_is_marked_stale() -> None:
    upstream = respx.get(settings.inpe_queimadas_wfs_url).mock(
        return_value=httpx.Response(200, json=payload())
    )
    fresh = await service.get_fire_hotspots(AsyncMock())
    service._cache.clear()
    upstream.mock(return_value=httpx.Response(502))
    previous = await service.get_fire_hotspots(AsyncMock())
    assert previous.metadata.status == "stale"
    assert previous.metadata.fetched_at == fresh.metadata.fetched_at
    assert previous.metadata.window_end == fresh.metadata.window_end
    assert previous.metadata.cql_filter == fresh.metadata.cql_filter


@respx.mock
@pytest.mark.parametrize(
    "body",
    [{}, {"features": []}, {"type": "FeatureCollection", "numberMatched": 1, "features": []}],
)
async def test_invalid_upstream_is_reported_as_failure(body: dict) -> None:
    respx.get(settings.inpe_queimadas_wfs_url).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(ProviderError):
        await service.get_fire_hotspots(AsyncMock())


@respx.mock
async def test_identify_keeps_window_and_orders_points_by_distance() -> None:
    data = payload()
    nearby = deepcopy(data["features"][0])
    nearby["properties"]["id_foco_bdq"] = 2
    nearby["geometry"]["coordinates"] = [-49.21, -2.46]
    data["features"].insert(0, nearby)
    upstream = respx.get(settings.inpe_queimadas_wfs_url).mock(
        return_value=httpx.Response(200, json=data)
    )
    at = datetime.now(UTC)
    result = await service.identify_fire_hotspots(
        AsyncMock(),
        level="country",
        parent_code=None,
        hours=48,
        latitude=-2.4636,
        longitude=-49.2089,
        tolerance=0.01,
        at=at,
    )
    assert result.features[0].id == "inpe:1891867420"
    cql = upstream.calls[0].request.url.params["cql_filter"]
    assert "BBOX(geometria,-49.218900,-2.473600,-49.198900,-2.453600,'EPSG:4326')" in cql
    assert f"data_hora_gmt <= '{service._iso(at)}'" in cql
    assert result.matched_count == 77157


async def test_old_or_naive_identify_window_requires_refresh() -> None:
    for at in [datetime.now(), datetime.now(UTC) - timedelta(hours=3)]:
        with pytest.raises(InvalidParameterError):
            await service.identify_fire_hotspots(
                AsyncMock(),
                level="country",
                parent_code=None,
                hours=48,
                latitude=-2,
                longitude=-49,
                tolerance=0.01,
                at=at,
            )


async def test_simultaneous_metadata_calls_are_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(service, "_fetch_wfs", fetch)
    await asyncio.gather(*(service.get_fire_hotspots(AsyncMock()) for _ in range(3)))
    assert fetch.call_count == 1


async def test_api_validation_and_error_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for query in ["?level=invalid", "?hours=0", "?hours=169", "?parent=abc", "?parent=35%27"]:
            assert (await client.get("/api/v1/fire-hotspots" + query)).status_code == 422
        invalid = await client.get(
            "/api/v1/fire-hotspots/identify",
            params={
                "latitude": 100,
                "longitude": 0,
                "tolerance": 1,
                "at": datetime.now(UTC).isoformat(),
            },
        )
        assert invalid.status_code == 422
        monkeypatch.setattr(
            service, "_fetch_wfs", AsyncMock(side_effect=ProviderError("INPE indisponível"))
        )
        response = await client.get("/api/v1/fire-hotspots")
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "provider_error"
