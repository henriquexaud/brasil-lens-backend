from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import httpx
import orjson
import pytest
import respx

from app.core.config import settings
from app.core.errors import ProviderError
from app.providers.inmet import alerts

_POLYGON = {
    "type": "Polygon",
    "coordinates": [[[-46.9, -23.8], [-46.3, -23.8], [-46.3, -23.3], [-46.9, -23.8]]],
}


def _entry(**overrides: object) -> dict:
    """Aviso no formato de /avisos/ativos, com os campos que o parser lê."""
    entry = {
        "id_aviso": 98765,
        "descricao": "Chuvas Intensas",
        "severidade": "Perigo",
        "aviso_cor": "#FF9900",
        "data_inicio": "2026-09-30T12:00:00-03:00",
        "data_fim": "2026-10-01T12:00:00-03:00",
        "poligono": orjson.dumps(_POLYGON).decode(),
        "geocodes": "3550308, 3304557",
        "riscos": ["Alagamentos"],
        "instrucoes": ["Procure um local abrigado."],
        "encerrado": False,
        **overrides,
    }
    return entry


def test_parses_an_active_alert_with_polygon_sent_as_a_string() -> None:
    record = alerts._to_alert(_entry())

    assert record is not None
    assert record.provider == "inmet"
    assert record.external_id == "98765"
    assert record.event == "Chuvas Intensas"
    assert record.severity == "Perigo"
    assert record.color == "#FF9900"
    assert record.polygon_geojson == _POLYGON
    assert record.affected_ibge_codes == ("3550308", "3304557")
    assert record.risks == ("Alagamentos",)
    assert record.instructions == ("Procure um local abrigado.",)
    assert record.onset == datetime(2026, 9, 30, 12, tzinfo=timezone(timedelta(hours=-3)))
    assert record.expires == datetime(2026, 10, 1, 15, tzinfo=UTC)


def test_polygon_already_decoded_is_accepted() -> None:
    record = alerts._to_alert(_entry(poligono=_POLYGON))
    assert record is not None
    assert record.polygon_geojson == _POLYGON


def test_expiry_is_the_source_end_date_so_stored_alerts_outlive_an_outage() -> None:
    record = alerts._to_alert(_entry(data_fim="2026-10-02T00:00:00Z"))
    assert record is not None
    assert record.expires == datetime(2026, 10, 2, tzinfo=UTC)


@pytest.mark.parametrize(
    "override",
    [
        {"id_aviso": None},
        {"data_inicio": None},
        {"data_fim": None},
        {"data_inicio": "ontem"},
        {"data_fim": 1759300000},
        {"poligono": None},
        {"poligono": "{não é json"},
        {"poligono": "[1, 2, 3]"},
        {"poligono": 42},
    ],
)
def test_alert_missing_id_dates_or_a_usable_polygon_is_discarded(override: dict) -> None:
    assert alerts._to_alert(_entry(**override)) is None


def test_missing_optional_fields_fall_back_without_inventing_data() -> None:
    record = alerts._to_alert(
        _entry(
            descricao=None,
            severidade=None,
            aviso_cor=None,
            geocodes=None,
            riscos=None,
            instrucoes=None,
        )
    )

    assert record is not None
    assert record.event == "Aviso meteorológico"
    assert record.severity == "Desconhecida"
    assert record.color is None
    assert record.affected_ibge_codes == ()
    assert record.risks == ()
    assert record.instructions == ()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3550308,3304557", ("3550308", "3304557")),
        (" 3550308 , , 3304557 ", ("3550308", "3304557")),
        ("3550308,SP,35.5", ("3550308",)),
        ("", ()),
        ("   ", ()),
        (None, ()),
        (3550308, ()),
    ],
)
def test_geocodes_keep_only_numeric_ibge_codes(raw: object, expected: tuple[str, ...]) -> None:
    assert alerts._split_codes(raw) == expected


@respx.mock
async def test_fetch_active_alerts_skips_closed_and_malformed_entries() -> None:
    respx.get(f"{settings.inmet_alerts_base_url}/avisos/ativos").mock(
        return_value=httpx.Response(
            200,
            json={
                "hoje": [
                    _entry(),
                    _entry(id_aviso=2, encerrado=True),
                    _entry(id_aviso=3, poligono=None),
                    "não é um aviso",
                ],
                "futuro": [_entry(id_aviso=4)],
            },
        )
    )
    async with httpx.AsyncClient(base_url=settings.inmet_alerts_base_url) as client:
        records = await alerts.fetch_active_alerts(client)

    assert [record.external_id for record in records] == ["98765"]


@respx.mock
async def test_fetch_active_alerts_with_nothing_active_is_an_empty_success() -> None:
    respx.get(f"{settings.inmet_alerts_base_url}/avisos/ativos").mock(
        return_value=httpx.Response(200, json={"hoje": []})
    )
    async with httpx.AsyncClient(base_url=settings.inmet_alerts_base_url) as client:
        assert await alerts.fetch_active_alerts(client) == []


@respx.mock
@pytest.mark.parametrize("body", [[], {"hoje": None}, {"hoje": {}}, {"avisos": []}, "texto"])
async def test_fetch_active_alerts_rejects_unexpected_shape(body: object) -> None:
    respx.get(f"{settings.inmet_alerts_base_url}/avisos/ativos").mock(
        return_value=httpx.Response(200, json=body)
    )
    async with httpx.AsyncClient(base_url=settings.inmet_alerts_base_url) as client:
        with pytest.raises(ProviderError, match="formato inesperado"):
            await alerts.fetch_active_alerts(client)


@respx.mock
async def test_fetch_active_alerts_reports_an_upstream_error_instead_of_an_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers import base

    monkeypatch.setattr(base, "RETRY_BACKOFF_SECONDS", 0)
    respx.get(f"{settings.inmet_alerts_base_url}/avisos/ativos").mock(
        return_value=httpx.Response(503)
    )
    async with httpx.AsyncClient(base_url=settings.inmet_alerts_base_url) as client:
        with pytest.raises(ProviderError, match="HTTP 503"):
            await alerts.fetch_active_alerts(client)
