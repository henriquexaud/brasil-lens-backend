"""Caso de uso do mapa: malha territorial, política de LOD e cache.

Regras de negócio do mapa base:
* política de LOD por nível territorial (overview vs detail);
* validação de compatibilidade entre `level` e `parent`;
* caching em memória local e no Redis da malha GeoJSON serializada;
* bounding box do escopo territorial.
"""

from __future__ import annotations

import orjson
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis_cache
from app.core.cache import TTLCache
from app.core.config import settings
from app.core.errors import InvalidParameterError, TerritoryNotFoundError
from app.core.logging import get_logger
from app.models import EXPECTED_PARENT_LEVEL, REQUIRES_PARENT, GeometryLOD, TerritoryLevel
from app.repositories import map_projection as map_repo
from app.repositories import territories as territories_repo
from app.schemas.map import (
    MapFeature,
    MapFeatureCollection,
    MapFeatureProperties,
    MapScope,
)

logger = get_logger(__name__)

# Política de detalhe: país e regiões usam a geometria mais agressiva. UFs e
# municípios usam a intermediária.
_DEFAULT_LOD: dict[TerritoryLevel, GeometryLOD] = {
    TerritoryLevel.COUNTRY: GeometryLOD.OVERVIEW,
    TerritoryLevel.REGION: GeometryLOD.OVERVIEW,
    TerritoryLevel.STATE: GeometryLOD.DETAIL,
    TerritoryLevel.MUNICIPALITY: GeometryLOD.DETAIL,
}

_cache: TTLCache[MapFeatureCollection] = TTLCache(
    ttl_seconds=settings.read_cache_ttl_seconds,
    max_entries=settings.read_cache_max_entries,
)
_version_cache: TTLCache[int] = TTLCache(ttl_seconds=60, max_entries=1)


def cache_stats() -> dict[str, int]:
    return _cache.stats()


def clear_cache() -> None:
    _cache.clear()
    _version_cache.clear()


async def data_version(session: AsyncSession) -> int:
    """Versão dos dados do mapa: muda a cada ingestão de territórios ou malhas."""
    cached = _version_cache.get("version")
    if cached is not None:
        return cached
    version = await map_repo.fetch_data_version(session)
    _version_cache.set("version", version)
    return version


def projection_key(
    *,
    level: TerritoryLevel,
    parent_code: str | None,
    lod: GeometryLOD | None,
    version: int,
) -> str:
    """Identidade de uma projeção da malha: chave do Redis e base do ETag."""
    return ":".join(
        (
            level.value,
            parent_code or "root",
            (lod or _DEFAULT_LOD[level]).value,
            str(version),
        )
    )


async def get_map(
    session: AsyncSession,
    *,
    level: TerritoryLevel,
    parent_code: str | None = None,
    lod: GeometryLOD | None = None,
    version: int | None = None,
) -> MapFeatureCollection:
    effective_lod = lod or _DEFAULT_LOD[level]
    _validate_scope(level, parent_code)

    if version is None:
        version = await data_version(session)
    redis_key = projection_key(
        level=level,
        parent_code=parent_code,
        lod=effective_lod,
        version=version,
    )
    cached = _cache.get(redis_key)
    if cached is not None:
        return cached

    cached_redis = await redis_cache.read("map-projection", redis_key, MapFeatureCollection)
    if cached_redis is not None:
        if len(cached_redis.features) <= settings.read_cache_max_features:
            _cache.set(redis_key, cached_redis)
        return cached_redis

    parent_id = await _resolve_parent(session, level, parent_code)

    rows = await map_repo.fetch_map_projection(
        session,
        level=level,
        lod=effective_lod,
        parent_id=parent_id,
    )

    features = [
        MapFeature(
            id=row.ibge_code,
            properties=MapFeatureProperties(
                ibge_code=row.ibge_code,
                name=row.name,
                level=TerritoryLevel(row.level),
                abbreviation=row.abbreviation,
                parent_code=row.parent_ibge_code,
                parent_name=row.parent_name,
            ),
            geometry=orjson.loads(row.geometry_json),
        )
        for row in rows
    ]

    response = MapFeatureCollection(
        scope=MapScope(
            level=level,
            parent=parent_code,
            lod=effective_lod,
            count=len(features),
        ),
        bbox=_scope_bbox(rows),
        features=features,
    )

    parent_feature: MapFeature | None = None
    if parent_code:
        parent_row = await map_repo.fetch_single_feature(session, parent_code, GeometryLOD.DETAIL)
        if parent_row:
            parent_feature = MapFeature(
                id=f"{parent_row.ibge_code}:detail",
                properties=MapFeatureProperties(
                    ibge_code=parent_row.ibge_code,
                    name=parent_row.name,
                    level=TerritoryLevel(parent_row.level),
                    abbreviation=parent_row.abbreviation,
                    parent_code=parent_row.parent_ibge_code,
                    parent_name=parent_row.parent_name,
                ),
                geometry=orjson.loads(parent_row.geometry_json),
            )
    response.parent_feature = parent_feature

    logger.info(
        "map.projection",
        extra={
            "level": level.value,
            "parent": parent_code,
            "lod": effective_lod.value,
            "features": len(features),
        },
    )

    if len(features) <= settings.read_cache_max_features:
        _cache.set(redis_key, response)
    await redis_cache.write("map-projection", redis_key, response, 86400)
    return response


def _scope_bbox(
    features: list[map_repo.MapFeatureRow],
) -> tuple[float, float, float, float] | None:
    """Bounding box que engloba todas as features do escopo."""
    w_list = [f.bbox[0] for f in features if f.bbox[0] is not None]
    s_list = [f.bbox[1] for f in features if f.bbox[1] is not None]
    e_list = [f.bbox[2] for f in features if f.bbox[2] is not None]
    n_list = [f.bbox[3] for f in features if f.bbox[3] is not None]
    if not (w_list and s_list and e_list and n_list):
        return None
    return (min(w_list), min(s_list), max(e_list), max(n_list))


def _validate_scope(level: TerritoryLevel, parent_code: str | None) -> None:
    if level in REQUIRES_PARENT and not parent_code:
        raise InvalidParameterError(
            f"O nível territorial '{level.value}' exige o parâmetro 'parent'.",
            level=level.value,
            required="parent",
        )
    if level is TerritoryLevel.COUNTRY and parent_code:
        raise InvalidParameterError(
            f"O nível territorial '{level.value}' não aceita o parâmetro 'parent'.",
            level=level.value,
            unexpected="parent",
        )


async def _resolve_parent(
    session: AsyncSession, level: TerritoryLevel, parent_code: str | None
) -> int | None:
    if not parent_code:
        return None
    expected_level = EXPECTED_PARENT_LEVEL.get(level)
    parent = await territories_repo.get_identity_by_code(session, parent_code)
    if parent is None:
        raise TerritoryNotFoundError(parent_code)
    parent_id, parent_level = parent
    if expected_level and parent_level != expected_level:
        raise InvalidParameterError(
            f"Território pai '{parent_code}' tem nível '{parent_level.value}', "
            f"mas '{level.value}' exige pai de nível '{expected_level.value}'.",
            parent=parent_code,
            parent_level=parent_level.value,
            expected_parent_level=expected_level.value,
        )
    return parent_id
