import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.schemas.hydrography import HydroFeatureCollection
from app.services import hydrography as service


def mock_ana_rivers_payload() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "FID": 101,
                    "NORIOCOMP": "Rio Amazonas",
                    "NUAREAMONT": 6042610.0,
                    "DEDOMINIAL": "Federal",
                    "ORGAO_GEST": "ANA",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-51.0, -0.05], [-50.5, 0.1]],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "FID": 102,
                    "NORIOCOMP": "Rio Amazonas",
                    "NUAREAMONT": 5800000.0,
                    "DEDOMINIAL": "Federal",
                    "ORGAO_GEST": "ANA",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-52.0, -0.1], [-51.0, -0.05]],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "FID": 103,
                    "NORIOCOMP": "Rio Tietê",
                    "NUAREAMONT": 72262.0,
                    "DEDOMINIAL": "Estadual",
                    "ORGAO_GEST": "DAEE-SP",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-46.0, -23.5], [-47.0, -22.5]],
                },
            },
        ],
    }


def mock_ana_water_bodies_payload() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "gid": 501,
                    "nmoriginal": "Represa de Sobradinho",
                    "detipomass": "Artificial",
                    "dedominial": "Federal",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-41.0, -9.5], [-40.8, -9.5], [-40.8, -9.3], [-41.0, -9.5]]],
                },
            }
        ],
    }


@pytest.fixture(autouse=True)
def clear_hydro_cache() -> None:
    service._cache.clear()
    service._locks.clear()


def test_consolidate_river_segments_groups_into_complete_rivers() -> None:
    raw = mock_ana_rivers_payload()["features"]
    consolidated = service._consolidate_river_segments(raw)

    assert len(consolidated) == 2

    amazonas = consolidated[0]
    assert amazonas.properties.name == "Rio Amazonas"
    assert amazonas.properties.drainage_area_km2 == 6042610.0
    assert amazonas.properties.segment_count == 2
    assert amazonas.geometry["type"] == "MultiLineString"
    assert amazonas.geometry["coordinates"] == [[[-52.0, -0.1], [-51.0, -0.05], [-50.5, 0.1]]]

    tiete = consolidated[1]
    assert tiete.properties.name == "Rio Tietê"
    assert tiete.properties.drainage_area_km2 == 72262.0
    assert tiete.properties.segment_count == 1


def test_major_rivers_snapshot_loaded_and_sorted() -> None:
    major_rivers = service._init_major_rivers()
    assert len(major_rivers) >= 50
    assert major_rivers[0].properties.name == "Rio Amazonas"
    assert (major_rivers[0].properties.drainage_area_km2 or 0) > 5000000
    for r in major_rivers:
        assert r.geometry["type"] == "MultiLineString"
        assert r.bbox is not None


async def test_country_only_returns_major_axes_with_reduced_geometry(monkeypatch) -> None:
    monkeypatch.setattr(service, "_fetch_water_bodies", AsyncMock(return_value=[]))
    response = await service.get_hydrography()
    result = HydroFeatureCollection.model_validate_json(response.body)
    assert 0 < result.metadata.river_count < len(service._MAJOR_RIVERS)
    assert len(response.body) < 100000
    assert sum(len(f.geometry["coordinates"]) for f in result.features) < 100
    assert all((feature.properties.drainage_area_km2 or 0) >= 200000 for feature in result.features)
    names = {f.properties.name for f in result.features}
    assert "Rio São Francisco" in names
    assert "Rio Paraná" in names
    assert "Rio Tietê" not in names


def test_viewport_clipping_and_simplification_preserve_crossing_rivers():
    bbox = (0, 0, 1, 1)
    assert service._clip_line([[-1, 0.5], [2, 0.5]], bbox) == [[[0, 0.5], [1, 0.5]]]
    assert service._clip_line([[-1, -1], [-2, -2]], bbox) == []
    points = [[0, 0], [0.25, 0.001], [0.5, 0], [0.75, -0.001], [1, 0]]
    assert service._simplify_line(points, 0.01) == [[0, 0], [1, 0]]
    assert len(service._simplify_line(points, 0.0001)) > 2


def test_zoom_filters_reduce_tributaries_and_small_lakes_at_country_scale():
    country = service.hydro_detail(4)
    state = service.hydro_detail(6)
    local = service.hydro_detail(10)
    assert country[0] > state[0] > local[0]
    assert country[1] > state[1] > local[1]
    assert country[2] > state[2] > local[2]


async def test_ana_outage_pauses_calls_and_serves_partial_snapshot(monkeypatch) -> None:
    rivers = AsyncMock(side_effect=httpx.ConnectError("ANA fora do ar"))
    bodies = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "_fetch_rivers", rivers)
    monkeypatch.setattr(service, "_fetch_water_bodies", bodies)

    first = await service.get_hydrography(bbox=(-47.0, -24.0, -46.0, -23.0), zoom=8)
    second = await service.get_hydrography(bbox=(-45.0, -23.0, -44.0, -22.0), zoom=8)

    assert first.status == second.status == "partial"
    assert rivers.call_count == bodies.call_count == 1


async def test_national_scale_shares_one_cache_entry_for_any_framing(monkeypatch):
    service._cache.clear()
    bodies = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "_fetch_water_bodies", bodies)
    first = await service.get_hydrography(zoom=4, bbox=(-60, -30, -40, -10))
    second = await service.get_hydrography(zoom=4.5, bbox=(-55, -25, -35, -5))
    assert first is second
    assert bodies.call_count == 1
    assert HydroFeatureCollection.model_validate_json(first.body).bbox == service.BRAZIL_BBOX
    await service.warm_up()
    assert bodies.call_count == 1
    service._cache.clear()


def test_river_segments_are_chained_from_the_headwater() -> None:
    lines = [[[2, 0], [3, 0]], [[0, 0], [1, 0]], [[1, 0], [2, 0]], [[1, 0], [1, 1]]]
    assert service._merge_lines(lines) == [[[0, 0], [1, 0], [2, 0], [3, 0]], [[1, 0], [1, 1]]]


def test_water_bodies_drop_rings_of_a_few_pixels() -> None:
    def square(west: float, south: float, side: float) -> list[list[float]]:
        return [
            [west, south],
            [west + side, south],
            [west + side, south + side],
            [west, south + side],
            [west, south],
        ]

    lake = {"type": "Polygon", "coordinates": [square(0, 0, 1), square(0.5, 0.5, 0.01)]}
    assert service._visible_rings(lake, 0.001) == {
        "type": "Polygon",
        "coordinates": [square(0, 0, 1)],
    }
    specks = {"type": "MultiPolygon", "coordinates": [[square(0, 0, 0.01)], [square(2, 2, 0.02)]]}
    assert service._visible_rings(specks, 0.001) is None


async def test_ana_batches_run_in_parallel_and_keep_order() -> None:
    in_flight = peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        if request.url.params.get("returnIdsOnly"):
            return httpx.Response(200, json={"objectIds": list(range(600))})
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        ids = request.url.params["objectIds"].split(",")
        return httpx.Response(200, json={"features": [{"id": i} for i in ids]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        features = await service._query_features(client, "https://ana.test/query", {})

    assert [feature["id"] for feature in features] == [str(i) for i in range(600)]
    assert peak == 3


async def test_route_serves_cached_json_in_camel_case_with_http_cache(monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    monkeypatch.setattr(service, "_fetch_water_bodies", AsyncMock(return_value=[]))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/hydrography?zoom=4")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert response.json()["metadata"]["riverCount"] > 0
