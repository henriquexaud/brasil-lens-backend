"""Configuração dos testes.

Há duas classes de teste, de propósito:

* **unitários** — normalização de fontes ambientais, cache e regras de clima.
  Rodam sem banco e sem rede, a partir de fixtures das fontes.
* **integração** (`@pytest.mark.db`) — persistência e consultas espaciais.
  Precisam de PostgreSQL/PostGIS; são puladas com mensagem clara
  quando o banco não está acessível.
"""

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
    # Fixtures de fontes externas não podem ler/gravar o cache da aplicação local.
    # Os testes de Redis injetam seu próprio cliente em memória.
    monkeypatch.setattr(settings, "redis_url", None)


@pytest.fixture(autouse=True)
def reset_source_cooldowns() -> None:
    # Uma fonte "fora do ar" num teste não pode silenciar a fonte no seguinte.
    cooldown.reset_all()


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Sessão de banco para os testes marcados com `db`."""
    engine = create_async_engine(settings.database_url, poolclass=None)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db_session:
            # Falha cedo e com mensagem útil se não houver banco.
            from sqlalchemy import text

            try:
                await db_session.execute(text("SELECT 1"))
            except Exception as exc:
                pytest.skip(f"PostgreSQL/PostGIS indisponível ({exc}). Rode 'make up' antes.")
            yield db_session
    finally:
        await engine.dispose()
