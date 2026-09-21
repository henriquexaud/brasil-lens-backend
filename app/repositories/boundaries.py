"""Áreas territoriais e páginas da malha municipal oficial."""

import json
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core import redis_cache
from app.core.cache import TTLCache
from app.models import GeometryLOD, Territory, TerritoryGeometry, TerritoryLevel
from app.schemas.map import MapFeature, MapFeatureCollection, MapFeatureProperties, MapScope

_areas: TTLCache[list[dict[str, Any]]] = TTLCache(86400, 2)


async def municipality_areas(session: AsyncSession) -> list[dict[str, Any]]:
    cached = _areas.get("municipalities")
    if cached is not None:
        return cached
    state = aliased(Territory)
    stmt = (
        select(
            Territory.ibge_code,
            Territory.name,
            state.abbreviation,
            func.ST_Area(cast(TerritoryGeometry.geom, Geography)) / 1_000_000,
        )
        .join(state, Territory.parent_id == state.id)
        .outerjoin(
            TerritoryGeometry,
            (TerritoryGeometry.territory_id == Territory.id)
            & (TerritoryGeometry.lod == GeometryLOD.CANONICAL),
        )
        .where(Territory.level == TerritoryLevel.MUNICIPALITY)
    )
    result = [
        dict(ibge_code=code, name=name, state=uf, area_km2=float(area) if area else None)
        for code, name, uf, area in (await session.execute(stmt)).all()
    ]
    _areas.set("municipalities", result)
    return result


async def municipality_map(
    session: AsyncSession,
    bbox: tuple[float, float, float, float] | None = None,
    *,
    parent: str | None = None,
    code: str | None = None,
    offset: int = 0,
    limit: int = 24,
) -> MapFeatureCollection:
    # Malha oficial intacta, em páginas pequenas; nunca geometria aproximada.
    key = f"canonical-v1:{parent}:{code}:{bbox}:{offset}:{limit}"
    cached = await redis_cache.read("municipality-map", key, MapFeatureCollection)
    if cached is not None:
        return cached
    state = aliased(Territory)
    stmt = (
        select(
            Territory.ibge_code,
            Territory.name,
            state.ibge_code,
            state.name,
            func.ST_AsGeoJSON(TerritoryGeometry.geom),
        )
        .join(state, Territory.parent_id == state.id)
        .join(TerritoryGeometry, TerritoryGeometry.territory_id == Territory.id)
        .where(
            Territory.level == TerritoryLevel.MUNICIPALITY,
            TerritoryGeometry.lod == GeometryLOD.CANONICAL,
        )
    )
    if parent:
        stmt = stmt.where(state.ibge_code == parent)
    if code:
        stmt = stmt.where(Territory.ibge_code == code)
    if bbox:
        stmt = stmt.where(
            func.ST_Intersects(TerritoryGeometry.geom, func.ST_MakeEnvelope(*bbox, 4326))
        )
        center = func.ST_SetSRID(
            func.ST_Point((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2), 4326
        )
        stmt = stmt.order_by(TerritoryGeometry.geom.op("<->")(center))
    else:
        # A capital aparece no primeiro lote do estado.
        stmt = stmt.order_by((Territory.id == state.capital_territory_id).desc().nullslast())
    stmt = stmt.order_by(Territory.ibge_code).offset(offset).limit(limit + 1)
    rows = (await session.execute(stmt)).all()
    features = [
        MapFeature(
            id=ibge_code,
            properties=MapFeatureProperties(
                ibge_code=ibge_code,
                name=name,
                parent_code=parent_code,
                parent_name=state_name,
                level=TerritoryLevel.MUNICIPALITY,
            ),
            geometry=json.loads(geometry),
        )
        for ibge_code, name, parent_code, state_name, geometry in rows[:limit]
    ]
    result = MapFeatureCollection(
        scope=MapScope(
            level=TerritoryLevel.MUNICIPALITY,
            parent=parent,
            lod=GeometryLOD.CANONICAL,
            count=len(features),
        ),
        features=features,
        next_offset=offset + limit if len(rows) > limit else None,
    )
    await redis_cache.write("municipality-map", key, result, 86400)
    return result


async def state_areas(session: AsyncSession) -> list[dict[str, Any]]:
    cached = _areas.get("states")
    if cached is not None:
        return cached
    stmt = (
        select(
            Territory.ibge_code,
            Territory.name,
            Territory.abbreviation,
            func.ST_Area(cast(TerritoryGeometry.geom, Geography)) / 1_000_000,
        )
        .outerjoin(
            TerritoryGeometry,
            (TerritoryGeometry.territory_id == Territory.id)
            & (TerritoryGeometry.lod == GeometryLOD.CANONICAL),
        )
        .where(Territory.level == TerritoryLevel.STATE)
    )
    result = [
        dict(ibge_code=code, name=name, state=uf, area_km2=float(area) if area else None)
        for code, name, uf, area in (await session.execute(stmt)).all()
    ]
    _areas.set("states", result)
    return result
