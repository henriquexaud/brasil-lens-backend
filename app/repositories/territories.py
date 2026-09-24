"""Acesso a dados de território.

Repositório devolve linhas/dataclasses. Nenhuma política (o que fazer quando
não há dado, como classificar) mora aqui — isso é responsabilidade do serviço.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.text import normalize_text
from app.models import GeometryLOD, Territory, TerritoryGeometry, TerritoryLevel


@dataclass(frozen=True, slots=True)
class TerritoryRow:
    """Território com o pai e a capital já resolvidos — evita N+1 no serviço."""

    ibge_code: str
    name: str
    level: TerritoryLevel
    abbreviation: str | None
    parent_ibge_code: str | None
    parent_name: str | None
    parent_level: TerritoryLevel | None
    capital_ibge_code: str | None
    capital_name: str | None
    bbox: tuple[float, float, float, float] | None


def _bbox(
    west: float | None, south: float | None, east: float | None, north: float | None
) -> tuple[float, float, float, float] | None:
    if None in (west, south, east, north):
        return None
    return (west, south, east, north)  # type: ignore[return-value]


def _select_with_relations() -> Select[tuple[Territory, Territory, Territory]]:
    parent = aliased(Territory, name="parent")
    capital = aliased(Territory, name="capital")
    return (
        select(
            Territory.ibge_code,
            Territory.name,
            Territory.level,
            Territory.abbreviation,
            Territory.bbox_west,
            Territory.bbox_south,
            Territory.bbox_east,
            Territory.bbox_north,
            parent.ibge_code.label("parent_ibge_code"),
            parent.name.label("parent_name"),
            parent.level.label("parent_level"),
            capital.ibge_code.label("capital_ibge_code"),
            capital.name.label("capital_name"),
        )
        .join(parent, Territory.parent_id == parent.id, isouter=True)
        .join(capital, Territory.capital_territory_id == capital.id, isouter=True)
    )


def _to_row(record: object) -> TerritoryRow:
    row = record  # tipagem de Row é dinâmica; acessos são por atributo nomeado
    return TerritoryRow(
        ibge_code=row.ibge_code,  # type: ignore[attr-defined]
        name=row.name,  # type: ignore[attr-defined]
        level=row.level,  # type: ignore[attr-defined]
        abbreviation=row.abbreviation,  # type: ignore[attr-defined]
        parent_ibge_code=row.parent_ibge_code,  # type: ignore[attr-defined]
        parent_name=row.parent_name,  # type: ignore[attr-defined]
        parent_level=row.parent_level,  # type: ignore[attr-defined]
        capital_ibge_code=row.capital_ibge_code,  # type: ignore[attr-defined]
        capital_name=row.capital_name,  # type: ignore[attr-defined]
        bbox=_bbox(
            row.bbox_west,  # type: ignore[attr-defined]
            row.bbox_south,  # type: ignore[attr-defined]
            row.bbox_east,  # type: ignore[attr-defined]
            row.bbox_north,  # type: ignore[attr-defined]
        ),
    )


def _search_terms(search: str) -> tuple[str, ColumnElement[Any], ColumnElement[Any]]:
    """Termo normalizado e as colunas comparáveis a ele (nome e sigla sem acento)."""
    name_col = func.coalesce(Territory.normalized_name, func.lower(Territory.name))
    abbr_col = func.coalesce(Territory.normalized_abbreviation, func.lower(Territory.abbreviation))
    return normalize_text(search), name_col, abbr_col


def _search_filter(search: str) -> ColumnElement[bool]:
    """Casa o termo no nome, a sigla exata ou o início da sigla."""
    clean, name_col, abbr_col = _search_terms(search)
    return or_(
        name_col.contains(clean, autoescape=True),
        abbr_col == clean,
        abbr_col.startswith(clean, autoescape=True),
    )


async def get_by_code(session: AsyncSession, ibge_code: str) -> TerritoryRow | None:
    """Busca por código IBGE — o identificador canônico da API pública."""
    stmt = _select_with_relations().where(Territory.ibge_code == ibge_code)
    record = (await session.execute(stmt)).first()
    return _to_row(record) if record is not None else None


async def get_weather_point(session: AsyncSession, ibge_code: str) -> tuple[float, float] | None:
    """Ponto interno da malha municipal: evita consultar no mar ou fora do território."""
    point = func.ST_PointOnSurface(TerritoryGeometry.geom)
    stmt = (
        select(func.ST_Y(point), func.ST_X(point))
        .join(Territory, Territory.id == TerritoryGeometry.territory_id)
        .where(Territory.ibge_code == ibge_code, TerritoryGeometry.lod == GeometryLOD.OVERVIEW)
    )
    row = (await session.execute(stmt)).first()
    return (float(row[0]), float(row[1])) if row else None


async def get_id_by_code(session: AsyncSession, ibge_code: str) -> int | None:
    stmt = select(Territory.id).where(Territory.ibge_code == ibge_code)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_level_by_code(session: AsyncSession, ibge_code: str) -> TerritoryLevel | None:
    stmt = select(Territory.level).where(Territory.ibge_code == ibge_code)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_territories(
    session: AsyncSession,
    *,
    level: TerritoryLevel | None = None,
    parent_ibge_code: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[TerritoryRow]:
    """Listagem paginada. Usa ix_territories_level_name / ix_territories_parent_id_name."""
    parent_filter = aliased(Territory, name="parent_filter")
    stmt = _select_with_relations()

    if level is not None:
        stmt = stmt.where(Territory.level == level)
    if parent_ibge_code is not None:
        stmt = stmt.join(parent_filter, Territory.parent_id == parent_filter.id).where(
            parent_filter.ibge_code == parent_ibge_code
        )
    if search:
        clean, name_col, abbr_col = _search_terms(search)
        stmt = stmt.where(_search_filter(search))

        # Pontuação de relevância (menor score = maior prioridade):
        # 0: Sigla exata ("SP")
        # 1: Nome exato ("sao paulo")
        # 2: Nome começa com termo ("sao")
        # 3: Palavra no meio do nome começa com o termo ("paulo" em "São Paulo")
        # 4: Sigla começa com o termo
        # 5: Termo contido no meio do nome
        score = case(
            (abbr_col == clean, 0),
            (name_col == clean, 1),
            (name_col.startswith(clean, autoescape=True), 2),
            (name_col.contains(f" {clean}", autoescape=True), 3),
            (abbr_col.startswith(clean, autoescape=True), 4),
            else_=5,
        )
        stmt = stmt.order_by(
            score,
            case((Territory.level == TerritoryLevel.STATE, 0), else_=1),
            func.length(Territory.name),
            Territory.name,
        )
    else:
        stmt = stmt.order_by(Territory.name)

    stmt = stmt.limit(limit).offset(offset)
    return [_to_row(record) for record in (await session.execute(stmt)).all()]


async def count_territories(
    session: AsyncSession,
    *,
    level: TerritoryLevel | None = None,
    parent_ibge_code: str | None = None,
    search: str | None = None,
) -> int:
    parent_filter = aliased(Territory, name="parent_filter")
    stmt = select(func.count()).select_from(Territory)
    if level is not None:
        stmt = stmt.where(Territory.level == level)
    if parent_ibge_code is not None:
        stmt = stmt.join(parent_filter, Territory.parent_id == parent_filter.id).where(
            parent_filter.ibge_code == parent_ibge_code
        )
    if search:
        stmt = stmt.where(_search_filter(search))
    return (await session.execute(stmt)).scalar_one()


async def children_summary(
    session: AsyncSession, ibge_code: str
) -> tuple[int, TerritoryLevel | None]:
    """Quantidade e nível dos filhos diretos, em uma única passada.

    Contagem em vez de coluna desnormalizada: com ix_territories_parent_id_name
    é um index scan sobre poucos milhares de linhas e não há risco de divergir
    do que está gravado. Contagem e nível vêm juntos porque sempre são usados
    juntos — eram duas queries para a mesma varredura.
    """
    parent = aliased(Territory, name="parent")
    stmt = (
        # Todos os filhos diretos compartilham o mesmo nível, então MIN()
        # apenas o extrai junto com a contagem, sem uma segunda varredura.
        select(func.count().label("total"), func.min(Territory.level).label("child_level"))
        .select_from(Territory)
        .join(parent, Territory.parent_id == parent.id)
        .where(parent.ibge_code == ibge_code)
    )
    record = (await session.execute(stmt)).one()
    total = record.total or 0
    if total == 0:
        return 0, None
    return total, TerritoryLevel(record.child_level)


_DISPERSED_POINTS_CACHE: dict[str, list[tuple[str, str, float, float]]] = {}


def _farthest_point_sampling(
    points: list[tuple[str, str, float, float]],
    start_idx: int = 0,
) -> list[tuple[str, str, float, float]]:
    n = len(points)
    if n <= 1:
        return points

    start_idx = max(0, min(start_idx, n - 1))
    lats = [p[2] for p in points]
    lons = [p[3] for p in points]

    mid_lat = sum(lats) / n
    cos_lat = math.cos(math.radians(mid_lat))
    adj_lons = [lon * cos_lat for lon in lons]

    visited = [False] * n
    visited[start_idx] = True
    selected = [start_idx]

    start_lat = lats[start_idx]
    start_lon = adj_lons[start_idx]

    min_dists = [(lats[i] - start_lat) ** 2 + (adj_lons[i] - start_lon) ** 2 for i in range(n)]

    for _ in range(1, n):
        best_idx = -1
        best_dist = -1.0
        for i in range(n):
            if not visited[i] and min_dists[i] > best_dist:
                best_dist = min_dists[i]
                best_idx = i
        if best_idx == -1:
            break
        visited[best_idx] = True
        selected.append(best_idx)
        new_lat = lats[best_idx]
        new_lon = adj_lons[best_idx]
        for i in range(n):
            if not visited[i]:
                d = (lats[i] - new_lat) ** 2 + (adj_lons[i] - new_lon) ** 2
                if d < min_dists[i]:
                    min_dists[i] = d

    for i in range(n):
        if not visited[i]:
            selected.append(i)

    return [points[i] for i in selected]


async def list_weather_points(
    session: AsyncSession,
    parent_code: str,
    offset: int,
    limit: int,
) -> list[tuple[str, str, float, float]]:
    if parent_code in _DISPERSED_POINTS_CACHE:
        all_points = _DISPERSED_POINTS_CACHE[parent_code]
        return all_points[offset : offset + limit]

    parent = aliased(Territory)
    point = func.ST_PointOnSurface(TerritoryGeometry.geom)
    stmt = (
        select(
            Territory.ibge_code,
            Territory.name,
            func.ST_Y(point),
            func.ST_X(point),
            parent.capital_territory_id,
            Territory.id,
        )
        .join(TerritoryGeometry, TerritoryGeometry.territory_id == Territory.id)
        .join(parent, Territory.parent_id == parent.id)
        .where(
            parent.ibge_code == parent_code,
            Territory.level == TerritoryLevel.MUNICIPALITY,
            TerritoryGeometry.lod == GeometryLOD.OVERVIEW,
        )
        .order_by(Territory.ibge_code)
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return []

    points = [(row[0], row[1], float(row[2]), float(row[3])) for row in rows]
    capital_id = rows[0][4]
    start_idx = 0
    if capital_id is not None:
        for idx, row in enumerate(rows):
            if row[5] == capital_id:
                start_idx = idx
                break

    ordered_points = _farthest_point_sampling(points, start_idx)
    _DISPERSED_POINTS_CACHE[parent_code] = ordered_points
    return ordered_points[offset : offset + limit]
