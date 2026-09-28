from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidParameterError, TerritoryNotFoundError
from app.models import GeometryLOD, TerritoryLevel
from app.schemas.map import MapFeatureCollection, MapLod
from app.services import map as map_service

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _isolated_cache() -> None:
    map_service.clear_cache()


@asynccontextmanager
async def _api(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    from app.api.deps import get_session
    from app.main import app

    async def override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


async def _map(
    session: AsyncSession, level: TerritoryLevel, parent_code: str | None = None
) -> MapFeatureCollection:
    body = await map_service.get_map_json(session, level=level, parent_code=parent_code)
    return MapFeatureCollection.model_validate_json(body)


async def _require_ingested_data(session: AsyncSession) -> None:
    states = (
        await session.execute(text("SELECT COUNT(*) FROM territories WHERE level = 'state'"))
    ).scalar_one()
    if states == 0:
        pytest.skip("Banco sem dados. Rode 'make ingest' antes dos testes de integração.")


async def test_mapa_inicial_carrega_ufs_com_geometria_simplificada(session: AsyncSession) -> None:
    await _require_ingested_data(session)
    collection = await _map(session, level=TerritoryLevel.STATE)
    assert collection.type == "FeatureCollection"
    assert collection.scope.count == 27
    assert collection.scope.lod is GeometryLOD.DETAIL
    assert len(collection.features) == 27
    assert {feature.properties.abbreviation for feature in collection.features} >= {"SP", "DF"}


async def test_mapa_municipal_carrega_apenas_o_estado_escolhido(session: AsyncSession) -> None:
    await _require_ingested_data(session)
    collection = await _map(session, level=TerritoryLevel.MUNICIPALITY, parent_code="35")
    assert collection.scope.lod is GeometryLOD.DETAIL
    assert collection.scope.count == 645
    assert all(feature.properties.parent_code == "35" for feature in collection.features)


async def test_nivel_municipal_exige_pai(session: AsyncSession) -> None:
    await _require_ingested_data(session)
    with pytest.raises(InvalidParameterError):
        await _map(session, level=TerritoryLevel.MUNICIPALITY)


async def test_pai_de_nivel_incompativel_e_recusado(session: AsyncSession) -> None:
    await _require_ingested_data(session)
    with pytest.raises(InvalidParameterError):
        await _map(session, level=TerritoryLevel.MUNICIPALITY, parent_code="3")


async def test_pai_inexistente_resulta_em_404(session: AsyncSession) -> None:
    await _require_ingested_data(session)
    with pytest.raises(TerritoryNotFoundError):
        await _map(session, level=TerritoryLevel.MUNICIPALITY, parent_code="99")


async def test_bbox_do_escopo_e_usado_no_enquadramento(session: AsyncSession) -> None:
    await _require_ingested_data(session)
    collection = await _map(session, level=TerritoryLevel.STATE)
    assert collection.bbox is not None
    west, south, east, north = collection.bbox
    assert west < east and south < north
    assert -75 < west < -65
    assert 3 < north < 6


async def test_pais_nao_aceita_pai(session: AsyncSession) -> None:
    await _require_ingested_data(session)
    with pytest.raises(InvalidParameterError):
        await _map(session, level=TerritoryLevel.COUNTRY, parent_code="35")
    root = await _map(session, level=TerritoryLevel.COUNTRY)
    assert root.scope.count == 1


def test_lod_publico_nao_expoe_geometria_canonica() -> None:
    public_lods = {member.value for member in MapLod}
    assert public_lods == {"overview", "detail"}
    assert GeometryLOD.CANONICAL.value not in public_lods
    assert {lod.to_geometry_lod() for lod in MapLod} == {
        GeometryLOD.OVERVIEW,
        GeometryLOD.DETAIL,
    }


async def test_geometrias_simplificadas_sao_menores_que_canonicas(
    session: AsyncSession,
) -> None:
    await _require_ingested_data(session)
    rows = await session.execute(
        text(
            """SELECT lod::text AS lod, SUM(vertex_count) AS vertices
                 FROM territory_geometries g
                 JOIN territories t ON t.id = g.territory_id
                WHERE t.level = 'municipality' GROUP BY lod"""
        )
    )
    vertices = {row.lod: int(row.vertices) for row in rows}
    assert vertices["overview"] < vertices["detail"] < vertices["canonical"]


async def test_malha_municipal_fica_no_cache_em_processo_como_json(
    session: AsyncSession,
) -> None:
    await _require_ingested_data(session)
    first = await map_service.get_map_json(
        session, level=TerritoryLevel.MUNICIPALITY, parent_code="35"
    )
    second = await map_service.get_map_json(
        session, level=TerritoryLevel.MUNICIPALITY, parent_code="35"
    )
    assert first is second
    assert map_service.cache_stats()["entries"] == 1


async def test_projecao_de_ufs_reutiliza_cache(session: AsyncSession) -> None:
    await _require_ingested_data(session)
    first = await map_service.get_map_json(session, level=TerritoryLevel.STATE)
    second = await map_service.get_map_json(session, level=TerritoryLevel.STATE)
    assert first is second
    assert map_service.cache_stats()["hits"] >= 1


async def test_etag_revalida_a_malha_sem_recalcular(session: AsyncSession) -> None:
    await _require_ingested_data(session)
    async with _api(session) as client:
        first = await client.get("/api/v1/map?level=state")
        assert first.status_code == 200
        etag = first.headers.get("etag")
        assert etag is not None and etag.startswith('W/"')
        assert first.headers["content-type"] == "application/json"
        assert first.headers["cache-control"].startswith("public, max-age=")
        body = first.json()
        assert body["scope"]["count"] == 27
        assert "parentFeature" in body and "ibgeCode" in body["features"][0]["properties"]
        second = await client.get("/api/v1/map?level=state", headers={"If-None-Match": etag})
        assert second.status_code == 304
