"""Testes do serviço e contrato de hidrografia (rios e massas d'água)."""

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

    # 2 trechos do Rio Amazonas + 1 trecho do Rio Tietê devem virar 2 rios consolidados
    assert len(consolidated) == 2

    # Priorização por porte hidrológico (Amazonas > Tietê)
    amazonas = consolidated[0]
    assert amazonas.properties.name == "Rio Amazonas"
    assert amazonas.properties.drainage_area_km2 == 6042610.0
    assert amazonas.properties.segment_count == 2
    assert amazonas.geometry["type"] == "MultiLineString"
    assert len(amazonas.geometry["coordinates"]) == 2

    tiete = consolidated[1]
    assert tiete.properties.name == "Rio Tietê"
    assert tiete.properties.drainage_area_km2 == 72262.0
    assert tiete.properties.segment_count == 1


def test_major_rivers_snapshot_loaded_and_sorted() -> None:
    major_rivers = service._init_major_rivers()
    assert len(major_rivers) >= 50
    # O primeiro deve ser o Rio Amazonas
    assert major_rivers[0].properties.name == "Rio Amazonas"
    assert (major_rivers[0].properties.drainage_area_km2 or 0) > 5000000
    # Todos devem ser MultiLineString e ter bounding box
    for r in major_rivers:
        assert r.geometry["type"] == "MultiLineString"
        assert r.bbox is not None


@pytest.mark.asyncio
async def test_country_only_returns_major_axes_with_reduced_geometry() -> None:
    session = AsyncMock()
    result = await service.get_hydrography(session, level="country", include_water_bodies=False)
    assert isinstance(result, HydroFeatureCollection)
    assert result.metadata.level == "country"
    assert 0 < result.metadata.river_count < len(service._MAJOR_RIVERS)
    assert len(result.model_dump_json()) < 200000
    assert all((feature.properties.drainage_area_km2 or 0) >= 200000 for feature in result.features)
    # Eixos principais persistem, tributários menores esperam o zoom.
    names = {f.properties.name for f in result.features}
    assert "Rio São Francisco" in names
    assert "Rio Paraná" in names
    assert "Rio Tietê" not in names


def test_viewport_clipping_and_simplification_preserve_crossing_rivers():
    bbox = (0, 0, 1, 1)
    # Nenhum vértice dentro: o segmento ainda cruza o viewport e deve aparecer.
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
    """Com a ANA fora do ar, outro recorte não espera um novo timeout: sai do snapshot."""
    rivers = AsyncMock(side_effect=httpx.ConnectError("ANA fora do ar"))
    bodies = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "_fetch_rivers", rivers)
    monkeypatch.setattr(service, "_fetch_water_bodies", bodies)

    first = await service.get_hydrography(
        AsyncMock(), level="state", bbox=(-47.0, -24.0, -46.0, -23.0), zoom=8
    )
    second = await service.get_hydrography(
        AsyncMock(), level="state", bbox=(-45.0, -23.0, -44.0, -22.0), zoom=8
    )

    assert first.metadata.status == second.metadata.status == "partial"
    assert rivers.call_count == 1
    assert bodies.call_count == 0
