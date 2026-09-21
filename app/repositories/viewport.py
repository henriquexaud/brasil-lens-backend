"""Resolução espacial do município e dos municípios que intersectam o mapa."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import GeometryLOD, Territory, TerritoryGeometry, TerritoryLevel


async def locate(session: AsyncSession, latitude: float, longitude: float) -> str | None:
    stmt = (
        select(Territory.ibge_code)
        .join(TerritoryGeometry, TerritoryGeometry.territory_id == Territory.id)
        .where(
            Territory.level == TerritoryLevel.MUNICIPALITY,
            TerritoryGeometry.lod == GeometryLOD.CANONICAL,
            func.ST_Covers(
                TerritoryGeometry.geom, func.ST_SetSRID(func.ST_Point(longitude, latitude), 4326)
            ),
        )
        .order_by(Territory.ibge_code)
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def weather_points(
    session: AsyncSession,
    bbox: tuple[float, float, float, float],
    offset: int,
    limit: int,
    parent: str | None = None,
) -> list[tuple[str, str, str, float, float]]:
    state = aliased(Territory)
    point = func.ST_PointOnSurface(TerritoryGeometry.geom)
    center = func.ST_SetSRID(func.ST_Point((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2), 4326)
    conditions = [
        Territory.level == TerritoryLevel.MUNICIPALITY,
        TerritoryGeometry.lod == GeometryLOD.CANONICAL,
        func.ST_Intersects(TerritoryGeometry.geom, func.ST_MakeEnvelope(*bbox, 4326)),
    ]
    if parent:
        conditions.append(Territory.ibge_code.startswith(parent))
    stmt = (
        select(
            Territory.ibge_code,
            Territory.name,
            state.abbreviation,
            func.ST_Y(point),
            func.ST_X(point),
        )
        .join(state, Territory.parent_id == state.id)
        .join(TerritoryGeometry, TerritoryGeometry.territory_id == Territory.id)
        .where(*conditions)
        .order_by(func.ST_Distance(point, center), Territory.ibge_code)
        .offset(offset)
        .limit(limit)
    )
    return [
        (code, name, uf or "", float(lat), float(lon))
        for code, name, uf, lat, lon in (await session.execute(stmt)).all()
    ]
