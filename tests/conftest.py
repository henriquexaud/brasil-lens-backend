from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import cooldown
from app.core.config import settings

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    with (FIXTURES / name).open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(autouse=True)
def isolate_shared_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "redis_url", None)


@pytest.fixture(autouse=True)
def reset_source_cooldowns() -> None:
    cooldown.reset_all()


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(settings.database_url, poolclass=None)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db_session:
            from sqlalchemy import text

            try:
                await db_session.execute(text("SELECT 1"))
            except Exception as exc:
                pytest.skip(f"PostgreSQL/PostGIS indisponível ({exc}). Rode 'make up' antes.")
            yield db_session
    finally:
        await engine.dispose()
