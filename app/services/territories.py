"""Casos de uso de território."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, TerritoryNotFoundError
from app.models import TerritoryLevel
from app.repositories import territories as territories_repo
from app.repositories.viewport import locate
from app.schemas.common import Pagination
from app.schemas.territory import (
    TerritoryDetail,
    TerritoryListResponse,
    TerritoryRef,
    TerritorySummary,
)


def _summary(row: territories_repo.TerritoryRow) -> TerritorySummary:
    return TerritorySummary(
        ibge_code=row.ibge_code,
        name=row.name,
        level=row.level,
        abbreviation=row.abbreviation,
        parent=(
            TerritoryRef(
                ibge_code=row.parent_ibge_code,
                name=row.parent_name or "",
                level=row.parent_level,
            )
            if row.parent_ibge_code
            else None
        ),
    )


async def list_territories(
    session: AsyncSession,
    *,
    level: TerritoryLevel | None = None,
    parent_code: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> TerritoryListResponse:
    if parent_code is not None:
        parent_level = await territories_repo.get_level_by_code(session, parent_code)
        if parent_level is None:
            raise TerritoryNotFoundError(parent_code)

    rows = await territories_repo.list_territories(
        session,
        level=level,
        parent_ibge_code=parent_code,
        search=search,
        limit=limit,
        offset=offset,
    )
    total = await territories_repo.count_territories(
        session, level=level, parent_ibge_code=parent_code, search=search
    )
    return TerritoryListResponse(
        territories=[_summary(row) for row in rows],
        pagination=Pagination(total=total, limit=limit, offset=offset),
    )


async def get_detail(session: AsyncSession, ibge_code: str) -> TerritoryDetail:
    row = await territories_repo.get_by_code(session, ibge_code)
    if row is None:
        raise TerritoryNotFoundError(ibge_code)
    children_count, children_level = await territories_repo.children_summary(session, row.ibge_code)
    summary = _summary(row)
    return TerritoryDetail(
        ibge_code=summary.ibge_code,
        name=summary.name,
        level=summary.level,
        abbreviation=summary.abbreviation,
        parent=summary.parent,
        capital=(
            TerritoryRef(
                ibge_code=row.capital_ibge_code,
                name=row.capital_name or "",
                level=TerritoryLevel.MUNICIPALITY,
            )
            if row.capital_ibge_code
            else None
        ),
        children_count=children_count,
        children_level=children_level,
        bbox=row.bbox,
    )


async def locate_territory(
    session: AsyncSession, latitude: float, longitude: float
) -> TerritoryDetail:
    code = await locate(session, latitude, longitude)
    if code is None:
        raise NotFoundError("Não encontramos um município brasileiro nessa localização.")
    return await get_detail(session, code)
