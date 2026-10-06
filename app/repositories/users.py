from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserSession


async def create(session: AsyncSession, user: User) -> User | None:
    stmt = (
        insert(User)
        .values(
            id=user.id,
            name=user.name,
            email=user.email,
            password_hash=user.password_hash,
            theme=user.theme,
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
