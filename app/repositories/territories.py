"""Acesso a dados de território.

Repositório devolve linhas/dataclasses. Nenhuma política (o que fazer quando
não há dado, como classificar) mora aqui — isso é responsabilidade do serviço.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import Territory, TerritoryLevel


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


async def get_by_code(session: AsyncSession, ibge_code: str) -> TerritoryRow | None:
    """Busca por código IBGE — o identificador canônico da API pública."""
    stmt = _select_with_relations().where(Territory.ibge_code == ibge_code)
    record = (await session.execute(stmt)).first()
    return _to_row(record) if record is not None else None


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
        # unaccent exigiria extensão extra; ILIKE resolve o MVP de forma previsível.
        stmt = stmt.where(Territory.name.ilike(f"%{search}%"))

    stmt = stmt.order_by(Territory.name).limit(limit).offset(offset)
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
        stmt = stmt.where(Territory.name.ilike(f"%{search}%"))
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
