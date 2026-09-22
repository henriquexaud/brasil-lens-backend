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


class _FakePipeline:
    def __init__(self, store: dict) -> None:
        self.store = store
        self.queued: list[tuple[str, bytes, int]] = []

    async def __aenter__(self) -> "_FakePipeline":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def set(self, key: str, value: bytes, ex: int) -> None:
        self.queued.append((key, value, ex))

    async def execute(self) -> None:
        for key, value, ex in self.queued:
            self.store[key] = (value, ex)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[bytes, int]] = {}
        self.mget_calls = 0

    def pipeline(self, transaction: bool) -> _FakePipeline:
        assert transaction is False
        return _FakePipeline(self.store)

    async def mget(self, keys: list[str]) -> list[bytes | None]:
        self.mget_calls += 1
        return [self.store[key][0] if key in self.store else None for key in keys]


async def test_many_keys_travel_in_one_round_trip(monkeypatch):
    connection = _FakeRedis()
    monkeypatch.setattr(redis_cache, "client", lambda: connection)
    one = FireHotspotDetails(features=[], matched_count=1)
    two = FireHotspotDetails(features=[], matched_count=2)
    await redis_cache.write_many("test", {"one": one, "two": two}, 600)
    assert {ex for _, ex in connection.store.values()} == {600}
    found = await redis_cache.read_many("test", ["two", "missing", "one"], FireHotspotDetails)
    assert found == [two, None, one]
    assert connection.mget_calls == 1


async def test_many_keys_without_redis_are_all_misses(monkeypatch):
    monkeypatch.setattr(redis_cache, "client", lambda: None)
    assert await redis_cache.read_many("test", ["a", "b"], FireHotspotDetails) == [None, None]
    await redis_cache.write_many(
        "test", {"a": FireHotspotDetails(features=[], matched_count=0)}, 60
    )
