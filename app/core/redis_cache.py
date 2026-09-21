"""Cache compartilhado opcional. Falha de Redis nunca impede consultar a fonte.

Somente dados públicos normalizados são guardados; a geolocalização do navegador
não passa por este cache. Chaves têm versão, namespace e expiração explícita.
"""

import hashlib
import time
import zlib
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


async def read(namespace: str, key: str, model: type[M]) -> M | None:
    connection = client()
    if connection is None:
        return None
    try:
        value = await connection.get(cache_key(namespace, key))
        return model.model_validate_json(zlib.decompress(value)) if value else None
    except RedisError:
        _failed()
    except (zlib.error, ValidationError, ValueError):
        logger.warning("cache.redis_invalid_value: %s", namespace)
    return None


async def write(namespace: str, key: str, value: BaseModel, ttl: int) -> None:
    connection = client()
    if connection is None:
        return
    payload = zlib.compress(value.model_dump_json().encode(), level=3)
    if len(payload) > 4_000_000:
        return
    try:
        await connection.set(cache_key(namespace, key), payload, ex=ttl)
    except RedisError:
        _failed()


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
