"""Instituições públicas de ensino superior por território (ADR-19).

O mapa pinta as matrículas pelo motor de indicadores (`context=education`);
aqui ficam a lista de instituições de um recorte e o detalhe de cada uma, lidos
das tabelas do Censo já ingeridas, sem consultar o INEP no request.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTLCache
from app.core.errors import InvalidParameterError, NotFoundError, TerritoryNotFoundError
from app.repositories import education as repo
from app.schemas.common import to_json
from app.schemas.education import (
    EducationCampus,
    EducationInstitutionDetail,
    EducationInstitutionSummary,
    EducationNetwork,
    EducationPlace,
    EducationTerritory,
)
from app.services.socioeconomic import parse_year

COUNTRY_CODE = "BR"

_cache: TTLCache[bytes] = TTLCache(ttl_seconds=300, max_entries=64)


def clear_cache() -> None:
    _cache.clear()


def _seat(row: Row[Any]) -> EducationPlace | None:
    if row.seat_code is None:
        return None
    return EducationPlace(code=row.seat_code, name=row.seat_name, state=row.seat_state)


async def _resolve_year(session: AsyncSession, year: str) -> tuple[int | None, list[int]]:
    requested = parse_year(year)
    years = await repo.fetch_years(session)
    if requested is None:
        return (years[-1] if years else None), years
    if requested not in years:
        raise InvalidParameterError(
            "Ano sem dados do Censo da Educação Superior.", parameter="year", available=years
        )
    return requested, years


async def get_territory(
    session: AsyncSession, code: str, year: str, network: EducationNetwork
) -> bytes:
    version = await repo.fetch_version(session)
    key = f"territory:{version}:{code}:{year}:{network}"
    cached = _cache.get(key)
    if cached is not None:
        return cached
    territory = await repo.fetch_territory(session, code)
    if territory is None:
        raise TerritoryNotFoundError(code)
    resolved, years = await _resolve_year(session, year)
    rows = (
        await repo.fetch_institutions_in(
            session,
            year=resolved,
            prefix="" if code == COUNTRY_CODE else code,
            category=None if network == "public" else network,
        )
        if resolved is not None
        else []
    )
    result = EducationTerritory(
        code=territory.ibge_code,
        name=territory.name,
        year=resolved,
        available_years=years,
        network=network,
        # Sem oferta presencial da rede no território: ausência, não zero (ADR-19).
        enrollment=sum(row.enrollment for row in rows) if rows else None,
        courses=sum(row.courses for row in rows) if rows else None,
        institutions=[
            EducationInstitutionSummary(
                code=row.code,
                name=row.name,
                acronym=row.acronym,
                category=row.category,
                organization=row.organization,
                seat=_seat(row),
                enrollment=row.enrollment,
                courses=row.courses,
                municipalities=row.municipalities,
            )
            for row in rows
        ],
    )
    body = to_json(result)
    _cache.set(key, body)
    return body


async def get_institution(session: AsyncSession, code: int, year: str) -> bytes:
    version = await repo.fetch_version(session)
    key = f"institution:{version}:{code}:{year}"
    cached = _cache.get(key)
    if cached is not None:
        return cached
    resolved, _ = await _resolve_year(session, year)
    row = await repo.fetch_institution(session, code, resolved) if resolved is not None else None
    if row is None:
        raise NotFoundError("Instituição pública não encontrada no Censo.", institution=code)
    campuses = await repo.fetch_campuses(session, code, row.year)
    result = EducationInstitutionDetail(
        code=row.code,
        year=row.year,
        name=row.name,
        acronym=row.acronym,
        category=row.category,
        organization=row.organization,
        seat=_seat(row),
        faculty=row.faculty,
        staff=row.staff,
        enrollment=sum(item.enrollment for item in campuses),
        distance_enrollment=row.distance_enrollment,
        courses=sum(item.courses for item in campuses),
        campuses=[
            EducationCampus(
                code=item.code,
                name=item.name,
                state=item.state,
                enrollment=item.enrollment,
                courses=item.courses,
                entrants=item.entrants,
                graduates=item.graduates,
            )
            for item in campuses
        ],
    )
    body = to_json(result)
    _cache.set(key, body)
    return body
