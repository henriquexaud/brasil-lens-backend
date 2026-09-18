"""Engine e sessões assíncronas."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

_engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
)

SessionFactory = async_sessionmaker(
    bind=_engine,
    expire_on_commit=False,
    autoflush=False,
)


def get_engine() -> AsyncEngine:
    return _engine


async def dispose_engine() -> None:
    await _engine.dispose()


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Sessão transacional para jobs e scripts (commit no sucesso, rollback no erro)."""
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
