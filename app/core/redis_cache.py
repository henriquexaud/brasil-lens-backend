"""Cache compartilhado opcional. Falha de Redis nunca impede consultar a fonte.

Somente dados públicos normalizados são guardados; a geolocalização do navegador
não passa por este cache. Chaves têm versão, namespace e expiração explícita.
"""

import hashlib
import time
import zlib
from collections.abc import Mapping, Sequence
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import get_logger

M = TypeVar("M", bound=BaseModel)
_client: Redis | None = None
_unavailable_until = 0.0
logger = get_logger(__name__)


def client() -> Redis | None:
    global _client
    if not settings.redis_url or time.monotonic() < _unavailable_until:
        return None
    if _client is None:
        _client = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=0.3,
            socket_timeout=0.5,
            retry=Retry(NoBackoff(), 0),
            max_connections=20,
        )
    return _client


def cache_key(namespace: str, key: str) -> str:
    return f"{settings.redis_cache_prefix}:{namespace}:{hashlib.sha256(key.encode()).hexdigest()}"


def _failed() -> None:
    global _unavailable_until
    _unavailable_until = time.monotonic() + 30
    logger.warning("cache.redis_unavailable: mantendo cache local e consulta à fonte")


def _decode(namespace: str, value: bytes | None, model: type[M]) -> M | None:
    if not value:
        return None
    try:
        return model.model_validate_json(zlib.decompress(value))
    except (zlib.error, ValidationError, ValueError):
        logger.warning("cache.redis_invalid_value: %s", namespace)
        return None


def _encode(value: BaseModel) -> bytes | None:
    payload = zlib.compress(value.model_dump_json().encode(), level=3)
    return payload if len(payload) <= 4_000_000 else None


async def read(namespace: str, key: str, model: type[M]) -> M | None:
    connection = client()
    if connection is None:
        return None
    try:
        value = await connection.get(cache_key(namespace, key))
    except RedisError:
        _failed()
        return None
    return _decode(namespace, value, model)


async def read_many(namespace: str, keys: Sequence[str], model: type[M]) -> list[M | None]:
    """Várias chaves numa ida ao Redis, na ordem pedida; ausentes viram `None`."""
    connection = client()
    if connection is None or not keys:
        return [None] * len(keys)
    try:
        values = await connection.mget([cache_key(namespace, key) for key in keys])
    except RedisError:
        _failed()
        return [None] * len(keys)
    return [_decode(namespace, value, model) for value in values]


async def write(namespace: str, key: str, value: BaseModel, ttl: int) -> None:
    connection = client()
    payload = _encode(value) if connection is not None else None
    if connection is None or payload is None:
        return
    try:
        await connection.set(cache_key(namespace, key), payload, ex=ttl)
    except RedisError:
        _failed()


async def write_many(namespace: str, values: Mapping[str, BaseModel], ttl: int) -> None:
    """Grava várias chaves num único pipeline, com a mesma expiração."""
    connection = client()
    if connection is None or not values:
        return
    try:
        async with connection.pipeline(transaction=False) as pipe:
            for key, value in values.items():
                if (payload := _encode(value)) is not None:
                    pipe.set(cache_key(namespace, key), payload, ex=ttl)
            await pipe.execute()
    except RedisError:
        _failed()


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
