"""Round-trip tipado, expiração explícita e indisponibilidade não fatal."""

from unittest.mock import AsyncMock

from redis.exceptions import ConnectionError

from app.core import redis_cache
from app.schemas.fire_hotspots import FireHotspotDetails


async def test_shared_cache_round_trip_with_ttl_and_versioned_key(monkeypatch):
    connection = AsyncMock()
    monkeypatch.setattr(redis_cache, "client", lambda: connection)
    value = FireHotspotDetails(features=[], matched_count=0)
    await redis_cache.write("test", "one", value, 600)
    args = connection.set.call_args
    assert args.kwargs["ex"] == 600
    assert args.args[0].startswith("brasil-lens:v3:test:")
    connection.get.return_value = args.args[1]
    assert await redis_cache.read("test", "one", FireHotspotDetails) == value
    assert redis_cache.cache_key("test", "one") != redis_cache.cache_key("test", "two")


async def test_cache_connection_failure_or_invalid_value_returns_miss(monkeypatch):
    connection = AsyncMock()
    monkeypatch.setattr(redis_cache, "client", lambda: connection)
    monkeypatch.setattr(redis_cache, "_unavailable_until", 0)
    connection.get.side_effect = ConnectionError("offline")
    assert await redis_cache.read("test", "one", FireHotspotDetails) is None
    connection.set.side_effect = ConnectionError("offline")
    await redis_cache.write("test", "one", FireHotspotDetails(features=[], matched_count=0), 60)
    connection.get.side_effect = None
    connection.get.return_value = b"invalid"
    assert await redis_cache.read("test", "one", FireHotspotDetails) is None
