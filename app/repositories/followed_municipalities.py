from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, Select, delete, func, select, update
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
    notifications_enabled: bool


def _select_rows(user_id: str) -> Select[Any]:
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
            FollowedMunicipality.notifications_enabled,
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
    stmt = _select_rows(user_id).order_by(
        FollowedMunicipality.created_at.desc(), FollowedMunicipality.id.desc()
    )
    return [_to_row(record) for record in await session.execute(stmt)]


async def get_for_user(session: AsyncSession, user_id: str, code: str) -> FollowedRow | None:
    stmt = _select_rows(user_id).where(FollowedMunicipality.municipality_code == code)
    record = (await session.execute(stmt)).first()
    return _to_row(record) if record is not None else None


async def follow(session: AsyncSession, user_id: str, code: str) -> bool:
    stmt = (
        insert(FollowedMunicipality)
        .values(user_id=user_id, municipality_code=code)
        .on_conflict_do_nothing(index_elements=["user_id", "municipality_code"])
        .returning(FollowedMunicipality.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def unfollow(session: AsyncSession, user_id: str, code: str) -> int:
    result = await session.execute(
        delete(FollowedMunicipality).where(
            FollowedMunicipality.user_id == user_id,
            FollowedMunicipality.municipality_code == code,
        )
    )
    return result.rowcount or 0


async def set_notifications(session: AsyncSession, user_id: str, code: str, enabled: bool) -> bool:
    stmt = (
        update(FollowedMunicipality)
        .where(
            FollowedMunicipality.user_id == user_id,
            FollowedMunicipality.municipality_code == code,
        )
        .values(
            notifications_enabled=enabled, notifications_opt_in_at=func.now() if enabled else None
        )
        .returning(FollowedMunicipality.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None
