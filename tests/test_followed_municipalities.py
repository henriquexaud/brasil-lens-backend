"""Municípios seguidos — a relação `usuário ↔ município`.

Tudo é integração (`db`): as regras que importam moram no banco (UNIQUE,
`ON CONFLICT`, CHECK) ou dependem do catálogo de territórios. Cada teste usa
um `user_id` próprio e a sessão nunca é commitada, então nada sobrevive.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidParameterError, TerritoryNotFoundError
from app.models import FollowedMunicipality
from app.services import followed_municipalities as service

pytestmark = pytest.mark.db

SAO_PAULO = "3550308"
BRASILIA = "5300108"


def _user() -> str:
    return f"test-{uuid.uuid4().hex[:12]}"


async def _require_municipalities(session: AsyncSession) -> None:
    found = (
        await session.execute(
            text("SELECT COUNT(*) FROM territories WHERE ibge_code IN (:a, :b)"),
            {"a": SAO_PAULO, "b": BRASILIA},
        )
    ).scalar_one()
    if found < 2:
        pytest.skip("Territórios não ingeridos. Rode 'make bootstrap' antes.")


@asynccontextmanager
async def _api(session: AsyncSession, user_id: str) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP com a sessão do teste (sem commit) e um usuário isolado."""
    from app.api.deps import get_current_user_id, get_session, get_write_session
    from app.main import app

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    async def override_user() -> str:
        return user_id

    overrides = {
        get_session: override_session,
        get_write_session: override_session,
        get_current_user_id: override_user,
    }
    app.dependency_overrides.update(overrides)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)


async def test_follow_list_unfollow_round_trip(session: AsyncSession) -> None:
    await _require_municipalities(session)
    user = _user()

    followed, created = await service.follow(session, user, SAO_PAULO)
    assert created
    # Nome e UF vêm do catálogo por JOIN — não são gravados no vínculo.
    assert (followed.name, followed.state_code, followed.state_abbreviation) == (
        "São Paulo",
        "35",
        "SP",
    )

    await service.follow(session, user, BRASILIA)
    listed = await service.list_followed(session, user)
    assert {item.municipality_code for item in listed.municipalities} == {SAO_PAULO, BRASILIA}

    await service.unfollow(session, user, SAO_PAULO)
    listed = await service.list_followed(session, user)
    assert [item.municipality_code for item in listed.municipalities] == [BRASILIA]

    await session.rollback()


async def test_follow_is_idempotent_and_per_user(session: AsyncSession) -> None:
    await _require_municipalities(session)
    alice, bob = _user(), _user()

    _, first = await service.follow(session, alice, SAO_PAULO)
    again, second = await service.follow(session, alice, SAO_PAULO)
    assert (first, second) == (True, False)
    assert again.municipality_code == SAO_PAULO

    # A lista de um usuário não enxerga a do outro.
    assert (await service.list_followed(session, bob)).municipalities == []
    assert len((await service.list_followed(session, alice)).municipalities) == 1

    await session.rollback()


async def test_database_refuses_a_duplicate_follow(session: AsyncSession) -> None:
    """A UNIQUE vale mesmo para quem escreve sem passar pelo serviço."""
    user = _user()
    session.add(FollowedMunicipality(user_id=user, municipality_code=SAO_PAULO))
    await session.flush()
    session.add(FollowedMunicipality(user_id=user, municipality_code=SAO_PAULO))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_only_existing_municipalities_can_be_followed(session: AsyncSession) -> None:
    await _require_municipalities(session)
    user = _user()
    with pytest.raises(TerritoryNotFoundError):
        await service.follow(session, user, "9999999")
    # Uma UF existe, mas não é um município.
    with pytest.raises(InvalidParameterError):
        await service.follow(session, user, "35")
    await session.rollback()


async def test_http_contract(session: AsyncSession) -> None:
    await _require_municipalities(session)
    base = "/api/v1/me/followed-municipalities"
    async with _api(session, _user()) as client:
        empty = await client.get(base)
        assert empty.status_code == 200
        assert empty.json() == {"municipalities": []}
        assert empty.headers["cache-control"] == "no-store"

        created = await client.put(f"{base}/{SAO_PAULO}")
        assert created.status_code == 201
        assert created.headers["location"].endswith(f"{base}/{SAO_PAULO}")
        body = created.json()
        assert body["municipalityCode"] == SAO_PAULO
        assert body["stateAbbreviation"] == "SP"
        assert "followedAt" in body

        assert (await client.put(f"{base}/{SAO_PAULO}")).status_code == 200

        listed = (await client.get(base)).json()["municipalities"]
        assert [item["municipalityCode"] for item in listed] == [SAO_PAULO]

        assert (await client.delete(f"{base}/{SAO_PAULO}")).status_code == 204
        # Deixar de seguir de novo não é erro: o estado pedido já vale.
        assert (await client.delete(f"{base}/{SAO_PAULO}")).status_code == 204
        assert (await client.get(base)).json() == {"municipalities": []}

        assert (await client.put(f"{base}/35")).status_code == 422  # não tem 7 dígitos
        assert (await client.put(f"{base}/9999999")).status_code == 404
    await session.rollback()
