"""A primeira ingestão e novas malhas devem chegar aos derivados do mapa."""

import json
from unittest.mock import AsyncMock, Mock

import pytest

from app.core import redis_cache
from app.core.cache import TTLCache
from app.repositories import boundaries, map_projection, territories


@pytest.mark.parametrize("read", [boundaries.municipality_areas, boundaries.state_areas])
async def test_areas_do_not_cache_empty_startup_and_follow_ingestion_version(monkeypatch, read):
    version = AsyncMock(return_value=1)
    monkeypatch.setattr(boundaries, "data_version", version)
    monkeypatch.setattr(boundaries, "_areas", TTLCache(86400, 2))
    result = Mock()
    result.all.return_value = []
    session = AsyncMock()
    session.execute.return_value = result
    assert await read(session) == []

    result.all.return_value = [("35", "São Paulo", "SP", 100.0)]
    first = await read(session)
    assert first[0]["area_km2"] == 100.0
    result.all.return_value = [("35", "São Paulo", "SP", 200.0)]
    assert await read(session) is first

    version.return_value = 2
    assert (await read(session))[0]["area_km2"] == 200.0
    assert session.execute.await_count == 3


async def test_municipal_weather_points_follow_ingestion_version(monkeypatch):
    version = AsyncMock(return_value=1)
    monkeypatch.setattr(territories, "data_version", version)
    monkeypatch.setattr(territories, "_DISPERSED_POINTS_CACHE", TTLCache(86400, 27))
    result = Mock()
    result.all.return_value = [("3500001", "A", -20, -45, None, 1)]
    session = AsyncMock()
    session.execute.return_value = result
    first = await territories.list_weather_points(session, "35", 0, 100)
    result.all.return_value = [("3500001", "A", -21, -46, None, 1)]
    assert await territories.list_weather_points(session, "35", 0, 100) == first

    version.return_value = 2
    assert await territories.list_weather_points(session, "35", 0, 100) == [
        ("3500001", "A", -21.0, -46.0)
    ]
    assert session.execute.await_count == 2


async def test_canonical_municipal_pages_use_new_cache_keys_after_ingestion(monkeypatch):
    version = AsyncMock(return_value=1)
    monkeypatch.setattr(boundaries, "data_version", version)
    saved = {}

    async def read(_namespace, key, _model):
        return saved.get(key)

    async def write(_namespace, key, value, _ttl):
        saved[key] = value

    monkeypatch.setattr(redis_cache, "read", read)
    monkeypatch.setattr(redis_cache, "write", write)
    result = Mock()
    result.all.return_value = []
    session = AsyncMock()
    session.execute.return_value = result
    assert (await boundaries.municipality_map(session, parent="35")).features == []
    assert not saved

    geometry = {"type": "MultiPolygon", "coordinates": []}
    result.all.return_value = [("3500001", "A", "35", "São Paulo", json.dumps(geometry))]
    first = await boundaries.municipality_map(session, parent="35")
    result.all.return_value = [("3500002", "B", "35", "São Paulo", json.dumps(geometry))]
    assert await boundaries.municipality_map(session, parent="35") is first

    version.return_value = 2
    second = await boundaries.municipality_map(session, parent="35")
    assert [feature.id for feature in second.features] == ["3500002"]
    assert len(saved) == 2


async def test_geography_version_reuses_a_single_query_until_expiration(monkeypatch):
    clock = Mock(return_value=100.0)
    monkeypatch.setattr("app.core.cache.time.monotonic", clock)
    monkeypatch.setattr(map_projection, "_version_cache", TTLCache(60, 1))
    fetch = AsyncMock(return_value=1)
    monkeypatch.setattr(map_projection, "fetch_data_version", fetch)
    session = AsyncMock()
    assert await map_projection.data_version(session) == 1
    fetch.return_value = 2
    assert await map_projection.data_version(session) == 1
    clock.return_value = 161.0
    assert await map_projection.data_version(session) == 2
    assert fetch.await_count == 2
