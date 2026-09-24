"""BDQueimadas oficial: WMS para cobertura completa, WFS para consulta pontual.

Schema confirmado em /queimadas/geoserver/wfs (DescribeFeatureType): os
campos id_0, id_1 e id_2 identificam país, UF e município IBGE, respectivamente.
Não confunde detecções por satélite com incêndios únicos ou fogo ainda ativo.
"""

import asyncio
import math
from datetime import UTC, datetime, timedelta
from weakref import WeakValueDictionary

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis_cache
from app.core.cache import TTLCache
from app.core.config import settings
from app.core.errors import InvalidParameterError, ProviderError
from app.core.logging import get_logger
from app.providers.inpe import fetch_features as _fetch_wfs
from app.schemas.fire_hotspots import (
    FireHotspotCollection,
    FireHotspotDetails,
    FireHotspotMetadata,
    FireScope,
)
from app.services.fire_scope import scope_filter as _scope_filter
from app.services.fire_scope import time_filter as _time_filter

logger = get_logger(__name__)
_cache: TTLCache[FireHotspotCollection] = TTLCache(settings.fire_hotspots_cache_ttl_seconds, 64)
_fallback: TTLCache[FireHotspotCollection] = TTLCache(3600, 64)
_failures: TTLCache[bool] = TTLCache(60, 64)
_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
DEFAULT_FIRE_HOURS = 24


async def get_fire_hotspots(
    session: AsyncSession,
    *,
    level: FireScope = "country",
    parent_code: str | None = None,
    hours: int = DEFAULT_FIRE_HOURS,
) -> FireHotspotCollection:
    scope = await _scope_filter(session, level, parent_code)
    key = f"{level}:{parent_code}:{hours}"
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached
        cached = await redis_cache.read("fire-metadata", key, FireHotspotCollection)
        if cached is not None:
            _cache.set(key, cached, ttl_seconds=30)
            return cached
        try:
            if _failures.get(key):
                raise ProviderError("INPE temporariamente indisponível. Tente em um minuto.")
            end = datetime.now(UTC)
            cql_filter = _time_filter(scope, end, hours)
            # Só a prévia vem em JSON. O WMS não tem esse limite e cobre todos os focos.
            features, total = await _fetch_wfs(cql_filter, 1)
            result = FireHotspotCollection(
                metadata=FireHotspotMetadata(
                    level=level,
                    parent_code=parent_code,
                    hotspot_count=total,
                    hours=hours,
                    fetched_at=datetime.now(UTC),
                    window_start=end - timedelta(hours=hours),
                    window_end=end,
                    latest_detection_at=features[0].properties.detected_at if features else None,
                    wms_url=settings.inpe_queimadas_wms_url,
                    cql_filter=cql_filter,
                ),
                features=features,
            )
            _cache.set(key, result)
            await redis_cache.write(
                "fire-metadata", key, result, settings.fire_hotspots_cache_ttl_seconds
            )
            _fallback.set(key, result)
            await redis_cache.write("fire-fallback", key, result, 3600)
            return result
        except ProviderError:
            if not _failures.get(key):
                _failures.set(key, True)
                logger.warning("fire.inpe_unavailable: %s", key, exc_info=True)
            previous = _fallback.get(key) or await redis_cache.read(
                "fire-fallback", key, FireHotspotCollection
            )
            if previous is not None:
                return previous.model_copy(
                    update={"metadata": previous.metadata.model_copy(update={"status": "stale"})}
                )
            raise


async def identify_fire_hotspots(
    session: AsyncSession,
    *,
    level: FireScope,
    parent_code: str | None,
    hours: int,
    latitude: float,
    longitude: float,
    tolerance: float,
    at: datetime,
) -> FireHotspotDetails:
    if at.tzinfo is None or not -timedelta(minutes=1) <= datetime.now(UTC) - at <= timedelta(
        hours=2
    ):
        raise InvalidParameterError("Atualize a camada antes de consultar este foco.", "at")
    scope = await _scope_filter(session, level, parent_code)
    west, east = max(-180, longitude - tolerance), min(180, longitude + tolerance)
    south, north = max(-90, latitude - tolerance), min(90, latitude + tolerance)
    cql_filter = (
        f"{_time_filter(scope, at, hours)}"
        f" AND BBOX(geometria,{west:.6f},{south:.6f},{east:.6f},{north:.6f},'EPSG:4326')"
    )
    features, total = await _fetch_wfs(cql_filter, 20)
    features.sort(
        key=lambda feature: (
            (feature.geometry.coordinates[0] - longitude) ** 2
            * math.cos(math.radians(latitude)) ** 2
            + (feature.geometry.coordinates[1] - latitude) ** 2
        )
    )
    return FireHotspotDetails(features=features, matched_count=total)
