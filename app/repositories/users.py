from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Row, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import (
    FollowedMunicipality,
    NotificationEvent,
    PushSubscription,
    Territory,
    User,
    UserSession,
)


async def create(session: AsyncSession, user: User) -> User | None:
    stmt = (
        insert(User)
        .values(
            id=user.id,
            name=user.name,
            email=user.email,
            password_hash=user.password_hash,
            theme=user.theme,
            terms_version=user.terms_version,
            terms_accepted_at=user.terms_accepted_at,
        )
        .on_conflict_do_nothing(index_elements=["email"])
        .returning(User)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def by_email(session: AsyncSession, email: str) -> User | None:
    return (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def by_token(session: AsyncSession, token_hash: str) -> User | None:
    stmt = (
        select(User)
        .join(UserSession, UserSession.user_id == User.id)
        .where(
            UserSession.token_hash == token_hash,
            UserSession.expires_at > datetime.now(UTC),
            User.email.is_not(None),
        )
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def add_session(
    session: AsyncSession, user_id: str, token_hash: str, expires: datetime
) -> None:
    await session.execute(delete(UserSession).where(UserSession.expires_at <= datetime.now(UTC)))
    session.add(UserSession(user_id=user_id, token_hash=token_hash, expires_at=expires))
    await session.flush()


async def remove_session(session: AsyncSession, token_hash: str) -> None:
    await session.execute(delete(UserSession).where(UserSession.token_hash == token_hash))


async def set_theme(session: AsyncSession, user_id: str, theme: str) -> User:
    stmt = update(User).where(User.id == user_id).values(theme=theme).returning(User)
    return (await session.execute(stmt)).scalar_one()


async def accept_terms(session: AsyncSession, user_id: str, version: str) -> User:
    stmt = (
        update(User)
        .where(User.id == user_id)
        .values(terms_version=version, terms_accepted_at=func.now())
        .returning(User)
    )
    return (await session.execute(stmt)).scalar_one()


async def remove(session: AsyncSession, user_id: str) -> None:
    # Sessões, acompanhamentos, inscrições push, eventos e entregas saem em cascata.
    await session.execute(delete(User).where(User.id == user_id))


async def followed_records(session: AsyncSession, user_id: str) -> list[Row[Any]]:
    municipality = aliased(Territory, name="municipality")
    state = aliased(Territory, name="state")
    stmt = (
        select(
            FollowedMunicipality.municipality_code,
            municipality.name.label("name"),
            state.abbreviation.label("state_abbreviation"),
            FollowedMunicipality.created_at.label("followed_at"),
            FollowedMunicipality.notifications_enabled,
            FollowedMunicipality.notifications_opt_in_at,
        )
        .join(
            municipality,
            municipality.ibge_code == FollowedMunicipality.municipality_code,
            isouter=True,
        )
        .join(state, state.id == municipality.parent_id, isouter=True)
        .where(FollowedMunicipality.user_id == user_id)
        .order_by(FollowedMunicipality.created_at)
    )
    return list(await session.execute(stmt))


async def device_records(session: AsyncSession, user_id: str) -> list[Row[Any]]:
    stmt = (
        select(PushSubscription.endpoint, PushSubscription.created_at)
        .where(PushSubscription.user_id == user_id)
        .order_by(PushSubscription.created_at)
    )
    return list(await session.execute(stmt))


async def notification_records(session: AsyncSession, user_id: str) -> list[Row[Any]]:
    stmt = (
        select(
            NotificationEvent.created_at,
            NotificationEvent.municipality_code,
            NotificationEvent.content,
        )
        .where(NotificationEvent.user_id == user_id)
        .order_by(NotificationEvent.created_at)
    )
    return list(await session.execute(stmt))


async def active_sessions(session: AsyncSession, user_id: str) -> int:
    stmt = select(func.count()).where(
        UserSession.user_id == user_id, UserSession.expires_at > datetime.now(UTC)
    )
    return int((await session.execute(stmt)).scalar_one())
