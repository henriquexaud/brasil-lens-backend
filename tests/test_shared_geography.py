"""Regressões da geografia compartilhada, sem depender de ingestão externa."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.main import app
from app.services import map as map_service

pytestmark = pytest.mark.db


@pytest_asyncio.fixture
async def geography_api(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    map_service.clear_cache()
    try:
        for code, name, level, parent in [
            ("TST", "País de teste", "country", None),
            ("9", "Região de teste", "region", "TST"),
            ("99", "Estado de teste", "state", "9"),
            ("9900001", "Município de teste A", "municipality", "99"),
            ("9900002", "Município de teste B", "municipality", "99"),
        ]:
            await session.execute(
                text("""
                    INSERT INTO territories (ibge_code, name, level, parent_id)
                    VALUES (:code, :name, CAST(:level AS territory_level),
                            (SELECT id FROM territories WHERE ibge_code = :parent))
                """),
                {"code": code, "name": name, "level": level, "parent": parent},
            )
        await session.execute(
            text("""
            UPDATE territories SET capital_territory_id = (
                SELECT id FROM territories WHERE ibge_code = '9900001'
            ) WHERE ibge_code = '99'
        """)
        )
        # Oceano: evita sobreposição caso o banco já tenha as malhas brasileiras.
        for code, west in [("99", -21), ("9900001", -20), ("9900002", -18)]:
            await session.execute(
                text("""
                    INSERT INTO territory_geometries (territory_id, lod, geom, vertex_count)
                    SELECT t.id, lod, ST_Multi(ST_MakeEnvelope(:west, -22, :east, -21, 4326)), 5
                      FROM territories t CROSS JOIN unnest(enum_range(NULL::geometry_lod)) lod
                     WHERE t.ibge_code = :code
                """),
                {"code": code, "west": west, "east": west + 1},
            )

        async def override() -> AsyncIterator[AsyncSession]:
            yield session

        app.dependency_overrides[get_session] = override
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
            yield api
    finally:
        app.dependency_overrides.pop(get_session, None)
        await session.rollback()
        map_service.clear_cache()


async def test_municipal_map_resolves_parent_id_and_keeps_parent_outline(geography_api):
    response = await geography_api.get("/api/v1/map?level=municipality&parent=99")
    assert response.status_code == 200
    data = response.json()
    assert data["scope"]["count"] == 2
    assert {f["properties"]["parentCode"] for f in data["features"]} == {"99"}
    assert data["parentFeature"]["properties"]["ibgeCode"] == "99"


async def test_optional_region_scope_is_preserved(geography_api):
    response = await geography_api.get("/api/v1/map?level=state&parent=9")
    assert response.status_code == 200
    assert [f["id"] for f in response.json()["features"]] == ["99"]


async def test_detail_and_geolocation_keep_shared_territorial_fields(geography_api):
    state = await geography_api.get("/api/v1/territories/99")
    assert state.status_code == 200
    assert state.json()["capital"]["ibgeCode"] == "9900001"
    assert state.json()["childrenCount"] == 2

    located = await geography_api.post(
        "/api/v1/territories/locate", json={"latitude": -21.5, "longitude": -19.5}
    )
    assert located.status_code == 200
    assert located.json()["ibgeCode"] == "9900001"
    assert located.json()["capital"] is None
    assert located.headers["cache-control"] == "no-store"
