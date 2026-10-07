from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TerritoryLevel


async def version(session: AsyncSession) -> str:
    result = await session.execute(
        text("""
        SELECT COALESCE(MAX(id), 0) FROM ingestion_runs
         WHERE job IN ('seed_indicators', 'import_indicators') AND status = 'succeeded'
    """)
    )
    return str(result.scalar_one())


async def indicator_id(session: AsyncSession, key: str) -> int | None:
    result = await session.execute(
        text("SELECT id FROM socioeconomic_indicators WHERE key = :key"), {"key": key}
    )
    return result.scalar_one_or_none()


async def catalog(session: AsyncSession, level: TerritoryLevel) -> list[dict[str, Any]]:
    result = await session.execute(
        text("""
        WITH coverage AS (
            SELECT v.indicator_id, v.reference_year
              FROM socioeconomic_values v JOIN territories t ON t.id = v.territory_id
             WHERE t.level = CAST(:level AS territory_level)
             GROUP BY v.indicator_id, v.reference_year
        )
        SELECT i.*, COALESCE(ARRAY_AGG(c.reference_year ORDER BY c.reference_year)
               FILTER (WHERE c.reference_year IS NOT NULL), '{}') AS available_years
          FROM socioeconomic_indicators i LEFT JOIN coverage c ON c.indicator_id = i.id
         GROUP BY i.id ORDER BY i.display_order, i.key
    """),
        {"level": level.value},
    )
    return [dict(row) for row in result.mappings()]


async def values(
    session: AsyncSession, *, level: TerritoryLevel, indicator_id: int, year: int | None
) -> list[tuple[str, str | None, Decimal | None]]:
    result = await session.execute(
        text("""
        SELECT t.ibge_code, p.ibge_code AS parent_code, v.value
          FROM territories t LEFT JOIN territories p ON p.id = t.parent_id
          LEFT JOIN socioeconomic_values v ON v.territory_id = t.id
           AND v.indicator_id = :indicator AND v.reference_year = :year
         WHERE t.level = CAST(:level AS territory_level)
         ORDER BY t.ibge_code
    """),
        {"level": level.value, "indicator": indicator_id, "year": year},
    )
    return [(row.ibge_code, row.parent_code, row.value) for row in result]


async def territory_values(
    session: AsyncSession, code: str, year: int | None
) -> dict[str, dict[str, Any]]:
    result = await session.execute(
        text("""
        SELECT DISTINCT ON (i.key) i.key, v.reference_year AS year, v.value, d.name AS source
          FROM socioeconomic_values v JOIN socioeconomic_indicators i ON i.id = v.indicator_id
          JOIN datasets d ON d.id = v.dataset_id JOIN territories t ON t.id = v.territory_id
         WHERE t.ibge_code = :code AND (CAST(:year AS smallint) IS NULL OR v.reference_year = :year)
         ORDER BY i.key, v.reference_year DESC
    """),
        {"code": code, "year": year},
    )
    return {row["key"]: dict(row) for row in result.mappings()}
