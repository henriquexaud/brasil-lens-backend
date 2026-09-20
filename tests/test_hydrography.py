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
async def test_get_hydrography_country_returns_instant_complete_rivers() -> None:
    session = AsyncMock()
    result = await service.get_hydrography(
        session, level="country", include_water_bodies=False
    )
    assert isinstance(result, HydroFeatureCollection)
    assert result.metadata.level == "country"
    assert result.metadata.river_count >= 50
    # Checa que Rio São Francisco e Rio Paraná estão completos
    names = {f.properties.name for f in result.features}
    assert "Rio São Francisco" in names
    assert "Rio Paraná" in names
    assert "Rio Tietê" in names
