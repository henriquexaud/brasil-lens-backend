from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession


async def fetch_version(session: AsyncSession) -> int:
    """Última ingestão concluída do Censo; muda a cada importação e vira chave de cache."""
    result = await session.execute(
        text(
            "SELECT COALESCE(MAX(id), 0) FROM ingestion_runs "
            "WHERE job = 'import_education' AND status = 'succeeded'"
        )
    )
    return int(result.scalar_one())


async def fetch_years(session: AsyncSession) -> list[int]:
    result = await session.execute(
        text("SELECT DISTINCT year FROM education_institutions ORDER BY year")
    )
    return [int(year) for year in result.scalars()]


async def fetch_territory(session: AsyncSession, code: str) -> Row[Any] | None:
    result = await session.execute(
        text("SELECT id, ibge_code, name, level FROM territories WHERE ibge_code = :code"),
        {"code": code},
    )
    return result.first()


async def fetch_institutions_in(
    session: AsyncSession, *, year: int, prefix: str, category: str | None
) -> list[Row[Any]]:
    """Instituições com oferta em municípios cujo código começa por `prefix`.

    O código IBGE do município começa pelo da UF, que começa pelo da região; o
    Brasil é o prefixo vazio.
    """
    result = await session.execute(
        text("""
        SELECT i.code, i.name, i.acronym, i.category, i.organization,
               s.ibge_code AS seat_code, s.name AS seat_name, sp.abbreviation AS seat_state,
               SUM(o.enrollment)::int AS enrollment, SUM(o.courses)::int AS courses,
               COUNT(*)::int AS municipalities
          FROM education_offers o
          JOIN territories m ON m.id = o.territory_id
          JOIN education_institutions i ON i.year = o.year AND i.code = o.institution_code
          LEFT JOIN territories s ON s.id = i.seat_territory_id
          LEFT JOIN territories sp ON sp.id = s.parent_id
         WHERE o.year = :year AND m.ibge_code LIKE :prefix || '%'
           AND (CAST(:category AS text) IS NULL OR i.category = :category)
         GROUP BY i.code, i.name, i.acronym, i.category, i.organization,
                  s.ibge_code, s.name, sp.abbreviation
         ORDER BY enrollment DESC, i.name
    """),
        {"year": year, "prefix": prefix, "category": category},
    )
    return list(result.all())


async def fetch_institution(session: AsyncSession, code: int, year: int) -> Row[Any] | None:
    result = await session.execute(
        text("""
        SELECT i.code, i.year, i.name, i.acronym, i.category, i.organization, i.faculty,
               i.staff, i.distance_enrollment, s.ibge_code AS seat_code, s.name AS seat_name,
               sp.abbreviation AS seat_state
          FROM education_institutions i
          LEFT JOIN territories s ON s.id = i.seat_territory_id
          LEFT JOIN territories sp ON sp.id = s.parent_id
         WHERE i.code = :code AND i.year = :year
    """),
        {"code": code, "year": year},
    )
    return result.first()


async def fetch_campuses(session: AsyncSession, code: int, year: int) -> list[Row[Any]]:
    result = await session.execute(
        text("""
        SELECT m.ibge_code AS code, m.name, p.abbreviation AS state, o.enrollment, o.courses,
               o.entrants, o.graduates
          FROM education_offers o
          JOIN territories m ON m.id = o.territory_id
          LEFT JOIN territories p ON p.id = m.parent_id
         WHERE o.institution_code = :code AND o.year = :year
         ORDER BY o.enrollment DESC, m.name
    """),
        {"code": code, "year": year},
    )
    return list(result.all())
