from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    PoliticalCandidate,
    PoliticalRelease,
    PoliticalResult,
    Territory,
    TerritoryLevel,
)


async def releases(session: AsyncSession) -> list[PoliticalRelease]:
    return list(
        (await session.execute(select(PoliticalRelease).order_by(PoliticalRelease.year))).scalars()
    )


async def results(
    session: AsyncSession,
    year: int,
    office: str,
    election_round: int,
    level: TerritoryLevel,
    parent: str | None,
) -> list[tuple[str, dict[str, Any]]]:
    statement = (
        select(PoliticalResult.territory_code, PoliticalResult.data)
        .join(Territory, Territory.ibge_code == PoliticalResult.territory_code)
        .where(
            PoliticalResult.year == year,
            PoliticalResult.office == office,
            PoliticalResult.round == election_round,
            Territory.level == level,
        )
    )
    if parent:
        statement = statement.where(Territory.ibge_code.startswith(parent))
    return [
        (code, data)
        for code, data in (
            await session.execute(statement.order_by(PoliticalResult.territory_code))
        ).all()
    ]


async def candidates(session: AsyncSession, year: int, ids: set[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(PoliticalCandidate.candidate_id, PoliticalCandidate.data).where(
                PoliticalCandidate.year == year, PoliticalCandidate.candidate_id.in_(ids)
            )
        )
    ).all()
    return {candidate_id: data for candidate_id, data in rows}


async def result(
    session: AsyncSession, year: int, office: str, election_round: int, code: str
) -> dict[str, Any] | None:
    return (
        await session.execute(
            select(PoliticalResult.data).where(
                PoliticalResult.year == year,
                PoliticalResult.office == office,
                PoliticalResult.round == election_round,
                PoliticalResult.territory_code == code,
            )
        )
    ).scalar_one_or_none()


async def elected(
    session: AsyncSession, year: int, office: str, code: str, offset: int, limit: int
) -> tuple[int, list[dict[str, Any]]]:
    filters = [
        PoliticalCandidate.year == year,
        PoliticalCandidate.office == office,
        PoliticalCandidate.elected_round.is_not(None),
    ]
    if office == "president":
        filters.append(PoliticalCandidate.scope_code == "BR")
    elif office in {"mayor", "councillor"}:
        if code != "BR":
            filters.append(PoliticalCandidate.scope_code.startswith(code))
    elif code != "BR":
        filters.append(PoliticalCandidate.scope_code == code[:2])
    total = (
        await session.execute(select(func.count()).select_from(PoliticalCandidate).where(*filters))
    ).scalar_one()
    rows = (
        await session.execute(
            select(PoliticalCandidate.data)
            .where(*filters)
            .order_by(
                PoliticalCandidate.scope_code,
                PoliticalCandidate.data["party"].astext,
                PoliticalCandidate.data["name"].astext,
                PoliticalCandidate.candidate_id,
            )
            .offset(offset)
            .limit(limit)
        )
    ).scalars()
    return total, list(rows)
