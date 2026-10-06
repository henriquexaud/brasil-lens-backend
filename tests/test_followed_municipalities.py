from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    FollowedMunicipalityNotFoundError,
    InvalidParameterError,
    TerritoryNotFoundError,
)
from app.models import FollowedMunicipality, User
from app.services import followed_municipalities as service

pytestmark = [pytest.mark.db, pytest.mark.ingested]

SAO_PAULO = "3550308"
BRASILIA = "5300108"


async def _user(session: AsyncSession) -> str:
    user_id = f"test-{uuid.uuid4().hex[:12]}"
    session.add(User(id=user_id, name="Conta de teste"))
    await session.flush()
    return user_id


@asynccontextmanager
async def _api(session: AsyncSession, user_id: str) -> AsyncIterator[AsyncClient]:
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
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Brasil-Lens-Client": "web"},
        ) as client:
            yield client
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)


async def test_follow_list_unfollow_round_trip(session: AsyncSession) -> None:
    user = await _user(session)

    followed, created = await service.follow(session, user, SAO_PAULO)
    assert created
    assert (followed.name, followed.state_code, followed.state_abbreviation) == (
        "São Paulo",
        "35",
        "SP",
    )
    assert followed.notifications_enabled is False

    await service.follow(session, user, BRASILIA)
    listed = await service.list_followed(session, user)
    assert {item.municipality_code for item in listed.municipalities} == {SAO_PAULO, BRASILIA}

    await service.unfollow(session, user, SAO_PAULO)
    listed = await service.list_followed(session, user)
    assert [item.municipality_code for item in listed.municipalities] == [BRASILIA]

    await session.rollback()


async def test_follow_is_idempotent_and_per_user(session: AsyncSession) -> None:
    alice, bob = await _user(session), await _user(session)

    _, first = await service.follow(session, alice, SAO_PAULO)
    again, second = await service.follow(session, alice, SAO_PAULO)
    assert (first, second) == (True, False)
    assert again.municipality_code == SAO_PAULO

    assert (await service.list_followed(session, bob)).municipalities == []
    assert len((await service.list_followed(session, alice)).municipalities) == 1

    await session.rollback()


async def test_database_refuses_a_duplicate_follow(session: AsyncSession) -> None:
    user = await _user(session)
    session.add(FollowedMunicipality(user_id=user, municipality_code=SAO_PAULO))
    await session.flush()
    session.add(FollowedMunicipality(user_id=user, municipality_code=SAO_PAULO))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_only_existing_municipalities_can_be_followed(session: AsyncSession) -> None:
    user = await _user(session)
    with pytest.raises(TerritoryNotFoundError):
        await service.follow(session, user, "9999999")
    with pytest.raises(InvalidParameterError):
        await service.follow(session, user, "35")
    await session.rollback()


async def test_set_notifications_toggles_and_requires_a_follow(session: AsyncSession) -> None:
    user = await _user(session)

    with pytest.raises(FollowedMunicipalityNotFoundError):
        await service.set_notifications(session, user, SAO_PAULO, False)

    await service.follow(session, user, SAO_PAULO)
    disabled = await service.set_notifications(session, user, SAO_PAULO, False)
    assert disabled.notifications_enabled is False
    followed = (
        await session.execute(
            select(FollowedMunicipality).where(
                FollowedMunicipality.user_id == user,
                FollowedMunicipality.municipality_code == SAO_PAULO,
            )
        )
    ).scalar_one()
    assert followed.notifications_opt_in_at is None

    enabled = await service.set_notifications(session, user, SAO_PAULO, True)
    assert enabled.notifications_enabled is True
    await session.refresh(followed)
    assert followed.notifications_opt_in_at is not None

    await service.set_notifications(session, user, SAO_PAULO, False)
    await session.refresh(followed)
    assert followed.notifications_opt_in_at is None

    await session.rollback()


async def test_http_contract(session: AsyncSession) -> None:
    base = "/api/v1/me/followed-municipalities"
    async with _api(session, await _user(session)) as client:
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
        assert body["notificationsEnabled"] is False
        assert "followedAt" in body

        assert (await client.put(f"{base}/{SAO_PAULO}")).status_code == 200

        listed = (await client.get(base)).json()["municipalities"]
        assert [item["municipalityCode"] for item in listed] == [SAO_PAULO]

        off = await client.post(f"{base}/{SAO_PAULO}/notifications", json={"enabled": False})
        assert off.status_code == 200
        assert off.json()["notificationsEnabled"] is False

        on = await client.post(f"{base}/{SAO_PAULO}/notifications", json={"enabled": True})
        assert on.status_code == 200
        assert on.json()["notificationsEnabled"] is True

        missing = await client.post(f"{base}/{BRASILIA}/notifications", json={"enabled": False})
        assert missing.status_code == 404

        assert (await client.delete(f"{base}/{SAO_PAULO}")).status_code == 204
        assert (await client.delete(f"{base}/{SAO_PAULO}")).status_code == 204
        assert (await client.get(base)).json() == {"municipalities": []}

        assert (await client.put(f"{base}/35")).status_code == 422
        assert (await client.put(f"{base}/9999999")).status_code == 404
    await session.rollback()
