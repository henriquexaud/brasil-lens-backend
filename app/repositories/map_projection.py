from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTLCache
from app.models import GeometryLOD, TerritoryLevel


@dataclass(slots=True)
class MapFeatureRow:
    ibge_code: str
    name: str
    level: str
    abbreviation: str | None
    parent_ibge_code: str | None
    parent_name: str | None
    geometry_json: str
    bbox: tuple[float | None, float | None, float | None, float | None]


_MAP_SQL = text(
    """
    SELECT t.ibge_code,
           t.name,
           t.level::text AS level,
           t.abbreviation,
           p.ibge_code AS parent_ibge_code,
           p.name AS parent_name,
           t.bbox_west,
           t.bbox_south,
           t.bbox_east,
           t.bbox_north,
           ST_AsGeoJSON(g.geom) AS geometry_json
      FROM territories t
      LEFT JOIN territories p ON p.id = t.parent_id
      JOIN territory_geometries g
             ON g.territory_id = t.id
            AND g.lod = CAST(:lod AS geometry_lod)
     WHERE t.level = CAST(:level AS territory_level)
       AND (CAST(:parent_id AS integer) IS NULL
            OR t.parent_id = CAST(:parent_id AS integer))
     ORDER BY t.name
    """
)


async def fetch_map_projection(
    session: AsyncSession,
    *,
    level: TerritoryLevel,
    lod: GeometryLOD,
    parent_id: int | None = None,
) -> list[MapFeatureRow]:
    result = await session.execute(
        _MAP_SQL,
        {
            "level": level.value,
            "lod": lod.value,
            "parent_id": parent_id,
        },
    )

    return [
        MapFeatureRow(
            ibge_code=row.ibge_code,
            name=row.name,
            level=row.level,
            abbreviation=row.abbreviation,
            parent_ibge_code=row.parent_ibge_code,
            parent_name=row.parent_name,
            geometry_json=row.geometry_json,
            bbox=(row.bbox_west, row.bbox_south, row.bbox_east, row.bbox_north),
        )
        for row in result
    ]


_SINGLE_FEATURE_SQL = text(
    """
    SELECT t.ibge_code,
           t.name,
           t.level::text AS level,
           t.abbreviation,
           p.ibge_code AS parent_ibge_code,
           p.name AS parent_name,
           t.bbox_west,
           t.bbox_south,
           t.bbox_east,
           t.bbox_north,
           ST_AsGeoJSON(g.geom) AS geometry_json
      FROM territories t
      LEFT JOIN territories p ON p.id = t.parent_id
      JOIN territory_geometries g
        ON g.territory_id = t.id
       AND g.lod = CAST(:lod AS geometry_lod)
     WHERE t.ibge_code = :code
     LIMIT 1
    """
)


async def fetch_single_feature(
    session: AsyncSession,
    ibge_code: str,
    lod: GeometryLOD = GeometryLOD.DETAIL,
) -> MapFeatureRow | None:
    result = (
        await session.execute(_SINGLE_FEATURE_SQL, {"code": ibge_code, "lod": lod.value})
    ).first()
    if not result:
        return None
    return MapFeatureRow(
        ibge_code=result.ibge_code,
        name=result.name,
        level=result.level,
        abbreviation=result.abbreviation,
        parent_ibge_code=result.parent_ibge_code,
        parent_name=result.parent_name,
        geometry_json=result.geometry_json,
        bbox=(result.bbox_west, result.bbox_south, result.bbox_east, result.bbox_north),
    )


_MAP_JOBS = ("import_territories", "import_geometries")
_version_cache: TTLCache[int] = TTLCache(ttl_seconds=60, max_entries=1)

_DATA_VERSION_SQL = text(
    """
    SELECT COALESCE(CAST(EXTRACT(EPOCH FROM MAX(finished_at)) AS bigint), 0)
      FROM ingestion_runs
     WHERE job = ANY(CAST(:jobs AS text[]))
       AND status IN ('succeeded', 'partial')
    """
)


async def fetch_data_version(session: AsyncSession) -> int:
    result = await session.execute(_DATA_VERSION_SQL, {"jobs": list(_MAP_JOBS)})
    return int(result.scalar_one())


async def data_version(session: AsyncSession) -> int:
    cached = _version_cache.get("version")
    if cached is not None:
        return cached
    version = await fetch_data_version(session)
    _version_cache.set("version", version)
    return version


def clear_version_cache() -> None:
    _version_cache.clear()
