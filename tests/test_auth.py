from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core import auth as security
from app.core.config import settings
from app.core.errors import AuthRateLimitedError
from app.main import app
from app.models import Territory, TerritoryLevel, User, UserSession


@pytest.fixture(autouse=True)
def reset_auth_attempts() -> None:
    security.reset_state()


@asynccontextmanager
async def _api(session: AsyncSession | None = None) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession | None]:
        yield session

    app.dependency_overrides[deps.get_session] = override
    app.dependency_overrides[deps.get_write_session] = override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Brasil-Lens-Client": "web"},
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(deps.get_session, None)
        app.dependency_overrides.pop(deps.get_write_session, None)


def test_password_hashes_are_salted_and_verify_exactly() -> None:
    first = security.hash_password(" senha com acento ç ")
    second = security.hash_password(" senha com acento ç ")
    assert first != second
    assert "senha" not in first
    assert security.verify_password(" senha com acento ç ", first)
    assert not security.verify_password("senha com acento ç", first)
    assert not security.verify_password("errada", first)
    assert not security.verify_password("errada", "hash inválido")


def test_auth_attempts_are_limited_and_reset_after_success() -> None:
    for _ in range(10):
        security.check_auth_attempt("alguem@exemplo.com")
    with pytest.raises(AuthRateLimitedError):
        security.check_auth_attempt("alguem@exemplo.com")
    security.check_auth_attempt("outra@exemplo.com")
    security.clear_auth_attempts("alguem@exemplo.com")
    security.check_auth_attempt("alguem@exemplo.com")


async def test_private_routes_require_authentication() -> None:
    async with _api() as client:
        for path in ("/auth/me", "/me/followed-municipalities"):
            response = await client.get(f"/api/v1{path}")
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "authentication_required"
            assert response.headers["cache-control"] == "no-store"


async def test_browser_writes_require_the_client_header_and_a_trusted_origin() -> None:
    # Sem override de escrita: a proteção é executada antes de abrir o banco.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in ("/auth/login", "/auth/register", "/auth/logout", "/me/preferences"):
            method = "PUT" if path == "/me/preferences" else "POST"
            response = await client.request(method, f"/api/v1{path}", json={})
            assert response.status_code == 403
        response = await client.post(
            "/api/v1/auth/logout",
            headers={"X-Brasil-Lens-Client": "web", "Origin": "https://nao-autorizado.test"},
        )
        assert response.status_code == 403


@pytest.mark.db
async def test_accounts_restore_theme_and_follows_and_keep_users_isolated(
    session: AsyncSession,
) -> None:
    country = Territory(ibge_code="999", level=TerritoryLevel.COUNTRY, name="País de teste")
    session.add(country)
    await session.flush()
    state = Territory(
        ibge_code="99", level=TerritoryLevel.STATE, name="Estado de teste", parent_id=country.id
    )
    session.add(state)
    await session.flush()
    session.add(
        Territory(
            ibge_code="9999998",
            level=TerritoryLevel.MUNICIPALITY,
            name="Município de teste",
            parent_id=state.id,
        )
    )
    await session.flush()
    email = f"{uuid.uuid4().hex}@exemplo.com"
    body = {
        "name": "  Ana  ",
        "email": f"  {email.upper()}  ",
        "password": "senha-forte-123",
        "theme": "dark",
    }
    async with _api(session) as client:
        response = await client.post("/api/v1/auth/register", json=body)
        assert response.status_code == 201
        alice = response.json()
        assert alice["name"] == "Ana"
        assert alice["email"] == email
        assert alice["theme"] == "dark"
        assert "password" not in response.text
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie and "max-age=2592000" in cookie
        token = client.cookies.get(security.SESSION_COOKIE)
        stored = (await session.execute(select(UserSession))).scalars().all()
        assert any(item.token_hash == security.hash_token(token) for item in stored)
        assert all(item.token_hash != token for item in stored)
        assert (await client.get("/api/v1/auth/me")).json() == alice
        followed = "/api/v1/me/followed-municipalities/9999998"
        assert (await client.put(followed, json={"userId": "local"})).status_code == 201
        assert (
            await client.post(f"{followed}/notifications", json={"enabled": False})
        ).status_code == 200
        theme = await client.put("/api/v1/me/preferences", json={"theme": "light"})
        assert theme.json()["theme"] == "light"
        assert (
            await client.put("/api/v1/me/preferences", json={"theme": "invalid"})
        ).status_code == 422
        assert (await client.post("/api/v1/auth/register", json=body)).status_code == 409
        logout = await client.post("/api/v1/auth/logout")
        assert logout.status_code == 204
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        replay = await client.get(
            "/api/v1/auth/me", headers={"Cookie": f"{security.SESSION_COOKIE}={token}"}
        )
        assert replay.status_code == 401
        assert (await client.post("/api/v1/auth/logout")).status_code == 204
        other = await client.post(
            "/api/v1/auth/register",
            json={**body, "email": f"{uuid.uuid4().hex}@exemplo.com", "theme": "dark"},
        )
        assert other.status_code == 201
        assert (await client.get("/api/v1/me/followed-municipalities")).json() == {
            "municipalities": []
        }
        assert (await client.delete(followed)).status_code == 204
        assert (await client.get("/api/v1/me/followed-municipalities")).json() == {
            "municipalities": []
        }
        login = await client.post(
            "/api/v1/auth/login", json={"email": email.upper(), "password": body["password"]}
        )
        assert login.status_code == 200
        assert login.json()["id"] == alice["id"]
        assert login.json()["theme"] == "light"
        restored = (await client.get("/api/v1/me/followed-municipalities")).json()["municipalities"]
        assert restored[0]["municipalityCode"] == "9999998"
        assert restored[0]["notificationsEnabled"] is False
        old_other_token = client.cookies.get(security.SESSION_COOKIE)
        await session.execute(
            update(UserSession)
            .where(UserSession.token_hash == security.hash_token(old_other_token))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        assert (await client.get("/api/v1/auth/me")).status_code == 401
    await session.rollback()


@pytest.mark.db
async def test_invalid_credentials_never_create_a_session_and_secure_cookie_in_production(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "name": "João",
        "email": f"{uuid.uuid4().hex}@exemplo.com",
        "password": "senha-forte-123",
    }
    async with _api(session) as client:
        assert (
            await client.post("/api/v1/auth/register", json={**body, "password": "curta"})
        ).status_code == 422
        await client.post("/api/v1/auth/register", json=body)
        await client.post("/api/v1/auth/logout")
        for email in (body["email"], "inexistente@exemplo.com"):
            response = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "incorreta"}
            )
            assert response.status_code == 401
            assert response.json()["error"]["message"] == "E-mail ou senha incorretos."
            assert "set-cookie" not in response.headers
        monkeypatch.setattr(settings, "app_env", "production")
        response = await client.post("/api/v1/auth/login", json=body)
        assert response.status_code == 200
        assert "; Secure" in response.headers["set-cookie"]
        user = (await session.execute(select(User).where(User.email == body["email"]))).scalar_one()
        assert user.password_hash != body["password"]
    await session.rollback()
