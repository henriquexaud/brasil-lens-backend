"""A paginação não altera as coordenadas da malha oficial."""

import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.core import redis_cache
from app.models import GeometryLOD
from app.repositories.boundaries import municipality_map


@pytest.mark.db
async def test_canonical_pages_are_faithful_disjoint_and_prioritize_capital(session, monkeypatch):
    monkeypatch.setattr(redis_cache, "read", AsyncMock(return_value=None))
    monkeypatch.setattr(redis_cache, "write", AsyncMock())
    first = await municipality_map(session, parent="35", limit=3)
    second = await municipality_map(session, parent="35", offset=3, limit=3)
    assert len(first.features) == len(second.features) == 3
    assert first.features[0].id == "3550308"
    assert first.scope.lod == GeometryLOD.CANONICAL
    assert first.next_offset == 3 and second.next_offset == 6
    assert not {f.id for f in first.features} & {f.id for f in second.features}
    for feature in first.features:
        source = (
            await session.execute(
                text("""
            SELECT ST_AsGeoJSON(g.geom) FROM territory_geometries g
            JOIN territories t ON t.id = g.territory_id
            WHERE t.ibge_code = :code AND g.lod = 'canonical'
        """),
                {"code": feature.id},
            )
        ).scalar_one()
        assert feature.geometry == json.loads(source)
    selected = await municipality_map(session, code="3550308", limit=1)
    assert selected.features == first.features[:1]
    assert selected.next_offset is None


@pytest.mark.db
async def test_viewport_paginates_all_intersecting_municipalities(session, monkeypatch):
    monkeypatch.setattr(redis_cache, "read", AsyncMock(return_value=None))
    monkeypatch.setattr(redis_cache, "write", AsyncMock())
    bbox = (-46.8, -23.7, -46.3, -23.3)
    expected = (
        (
            await session.execute(
                text("""
        SELECT t.ibge_code FROM territories t
        JOIN territory_geometries g ON g.territory_id = t.id
        WHERE t.level = 'municipality' AND g.lod = 'canonical'
        AND ST_Intersects(g.geom, ST_MakeEnvelope(:w, :s, :e, :n, 4326))
    """),
                dict(zip(("w", "s", "e", "n"), bbox, strict=True)),
            )
        )
        .scalars()
        .all()
    )
    seen = []
    offset = 0
    while True:
        page = await municipality_map(session, bbox, offset=offset, limit=3)
        seen.extend(f.id for f in page.features)
        if page.next_offset is None:
            break
        offset = page.next_offset
    assert len(seen) == len(set(seen))
    assert set(seen) == set(expected)
