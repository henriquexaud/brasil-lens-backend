"""Acesso a dados dos municípios seguidos.

Como nos demais repositórios, nenhuma política mora aqui: quem decide o que é
404 ou 400 é o serviço.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, Select, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import FollowedMunicipality, Territory


@dataclass(frozen=True, slots=True)
class FollowedRow:
    municipality_code: str
    name: str | None
    state_code: str | None
    state_name: str | None
    state_abbreviation: str | None
    followed_at: datetime


def _select_rows(user_id: str) -> Select[Any]:
    """Vínculos do usuário com nome e UF resolvidos por JOIN.

    LEFT JOIN: um código que sumiu do catálogo continua na lista (com nome
    nulo) em vez de desaparecer em silêncio — é o usuário quem o remove.
    """
    municipality = aliased(Territory, name="municipality")
    state = aliased(Territory, name="state")
    return (
        select(
            FollowedMunicipality.municipality_code,
            municipality.name.label("name"),
            state.ibge_code.label("state_code"),
            state.name.label("state_name"),
            state.abbreviation.label("state_abbreviation"),
            FollowedMunicipality.created_at.label("followed_at"),
        )
        .join(
            municipality,
            municipality.ibge_code == FollowedMunicipality.municipality_code,
            isouter=True,
        )
        .join(state, state.id == municipality.parent_id, isouter=True)
        .where(FollowedMunicipality.user_id == user_id)
    )


def _to_row(record: Row[Any]) -> FollowedRow:
    return FollowedRow(**record._asdict())


async def list_for_user(session: AsyncSession, user_id: str) -> list[FollowedRow]:
    """Mais recentes primeiro, como as visualizações salvas."""
    stmt = _select_rows(user_id).order_by(
        FollowedMunicipality.created_at.desc(), FollowedMunicipality.id.desc()
    )
    return [_to_row(record) for record in await session.execute(stmt)]


async def get_for_user(session: AsyncSession, user_id: str, code: str) -> FollowedRow | None:
    stmt = _select_rows(user_id).where(FollowedMunicipality.municipality_code == code)
    record = (await session.execute(stmt)).first()
    return _to_row(record) if record is not None else None


async def follow(session: AsyncSession, user_id: str, code: str) -> bool:
    """Cria o vínculo se ainda não existe. Devolve `True` se criou.

    `ON CONFLICT DO NOTHING` sobre a UNIQUE `(user_id, municipality_code)`:
    dois cliques simultâneos não viram erro nem linha duplicada — o banco é
    quem arbitra, não uma checagem prévia sujeita a corrida.
    """
    stmt = (
        insert(FollowedMunicipality)
        .values(user_id=user_id, municipality_code=code)
        .on_conflict_do_nothing(index_elements=["user_id", "municipality_code"])
        .returning(FollowedMunicipality.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def unfollow(session: AsyncSession, user_id: str, code: str) -> int:
    """Devolve quantas linhas foram removidas (0 = não seguia)."""
    result = await session.execute(
        delete(FollowedMunicipality).where(
            FollowedMunicipality.user_id == user_id,
            FollowedMunicipality.municipality_code == code,
        )
    )
    return result.rowcount or 0
