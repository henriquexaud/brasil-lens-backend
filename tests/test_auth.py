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
from app.core.legal import TERMS_VERSION
from app.main import app
from app.models import (
    FollowedMunicipality,
    PushSubscription,
    Territory,
    TerritoryLevel,
    User,
    UserSession,
)


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


def test_failures_from_one_ip_do_not_lock_the_account_for_another() -> None:
    for _ in range(security.ACCOUNT_LIMIT):
        security.check_auth_attempt("vitima@exemplo.com", "198.51.100.1")
    with pytest.raises(AuthRateLimitedError):
        security.check_auth_attempt("vitima@exemplo.com", "198.51.100.1")
    security.check_auth_attempt("vitima@exemplo.com", "203.0.113.7")


def test_one_ip_cannot_spray_many_emails_nor_evict_other_counters() -> None:
    for number in range(security.IP_LIMIT):
        security.check_auth_attempt(f"alvo{number}@exemplo.com", "198.51.100.2")
    with pytest.raises(AuthRateLimitedError):
        security.check_auth_attempt("outro@exemplo.com", "198.51.100.2")
    security.check_auth_attempt("outro@exemplo.com", "203.0.113.8")


def test_the_window_is_fixed_so_insisting_does_not_extend_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1000.0
    monkeypatch.setattr("time.monotonic", lambda: now)
    for _ in range(security.ACCOUNT_LIMIT):
        security.check_auth_attempt("fixa@exemplo.com", "198.51.100.3")
        now += 25
    with pytest.raises(AuthRateLimitedError):
        security.check_auth_attempt("fixa@exemplo.com", "198.51.100.3")
    now += security.WINDOW_SECONDS - 250 + 1
    security.check_auth_attempt("fixa@exemplo.com", "198.51.100.3")


def test_the_email_lock_escalates_and_ignores_the_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1000.0
    monkeypatch.setattr("time.monotonic", lambda: now)
    email = "alvo@exemplo.com"
    # A franquia de erros de digitação não tranca, mesmo trocando de IP a cada tentativa.
    for index in range(security.EMAIL_FREE_FAILURES):
        security.check_auth_attempt(email, f"203.0.113.{index}")
        security.record_auth_failure(email)
    # A falha seguinte tranca o e-mail; forjar um IP novo não contorna o bloqueio.
    security.check_auth_attempt(email, "203.0.113.200")
    security.record_auth_failure(email)
    with pytest.raises(AuthRateLimitedError):
        security.check_auth_attempt(email, "198.51.100.9")
    # Passado o primeiro degrau, aceita de novo; a nova falha tranca por mais tempo (escada).
    now += security._EMAIL_LOCK_LADDER[0] + 1
    security.check_auth_attempt(email, "198.51.100.9")
    security.record_auth_failure(email)
    now += security._EMAIL_LOCK_LADDER[0] + 1
    with pytest.raises(AuthRateLimitedError):
        security.check_auth_attempt(email, "198.51.100.9")


def test_a_successful_auth_clears_the_email_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1000.0
    monkeypatch.setattr("time.monotonic", lambda: now)
    email = "perdoa@exemplo.com"
    for _ in range(security.EMAIL_FREE_FAILURES + 1):
        security.check_auth_attempt(email)
        security.record_auth_failure(email)
    with pytest.raises(AuthRateLimitedError):
        security.check_auth_attempt(email)
    security.clear_auth_attempts(email)
    now += 1
    security.check_auth_attempt(email)


async def test_private_routes_require_authentication() -> None:
    async with _api() as client:
        for path in ("/auth/me", "/me/followed-municipalities", "/me/data"):
            response = await client.get(f"/api/v1{path}")
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "authentication_required"
            assert response.headers["cache-control"] == "no-store"


async def test_browser_writes_require_the_client_header_and_a_trusted_origin() -> None:
    # Sem override de escrita: a proteção é executada antes de abrir o banco.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in (
            "/auth/login",
            "/auth/register",
            "/auth/logout",
            "/me/preferences",
            "/me/terms",
            "/me/delete",
        ):
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
        "acceptTerms": True,
    }
    async with _api(session) as client:
        response = await client.post("/api/v1/auth/register", json=body)
        assert response.status_code == 201
        alice = response.json()
        assert alice["name"] == "Ana"
        assert alice["email"] == email
        assert alice["theme"] == "dark"
        assert alice["termsAccepted"] is True
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
        "acceptTerms": True,
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
        response = await client.post(
            "/api/v1/auth/login", json={"email": body["email"], "password": body["password"]}
        )
        assert response.status_code == 200
        assert "; Secure" in response.headers["set-cookie"]
        user = (await session.execute(select(User).where(User.email == body["email"]))).scalar_one()
        assert user.password_hash != body["password"]
    await session.rollback()


def _account(**extra: object) -> dict[str, object]:
    return {
        "name": "Bia",
        "email": f"{uuid.uuid4().hex}@exemplo.com",
        "password": "senha-forte-123",
        "acceptTerms": True,
        **extra,
    }


@pytest.mark.db
async def test_signup_requires_accepting_the_terms_and_records_the_version(
    session: AsyncSession,
) -> None:
    async with _api(session) as client:
        body = _account()
        for refused in ({"acceptTerms": False}, {"acceptTerms": None}):
            response = await client.post("/api/v1/auth/register", json={**body, **refused})
            assert response.status_code == 422
        missing = {key: value for key, value in body.items() if key != "acceptTerms"}
        assert (await client.post("/api/v1/auth/register", json=missing)).status_code == 422
        assert (await client.post("/api/v1/auth/register", json=body)).status_code == 201
        user = (await session.execute(select(User).where(User.email == body["email"]))).scalar_one()
        assert user.terms_version == TERMS_VERSION
        assert user.terms_accepted_at is not None
    await session.rollback()


@pytest.mark.db
async def test_accounts_without_the_current_terms_accept_them_after_login(
    session: AsyncSession,
) -> None:
    body = _account()
    async with _api(session) as client:
        await client.post("/api/v1/auth/register", json=body)
        await session.execute(
            update(User)
            .where(User.email == body["email"])
            .values(terms_version="2020-01-01", terms_accepted_at=None)
        )
        assert (await client.get("/api/v1/auth/me")).json()["termsAccepted"] is False
        accepted = await client.post("/api/v1/me/terms")
        assert accepted.status_code == 200
        assert accepted.json()["termsAccepted"] is True
        assert accepted.headers["cache-control"] == "no-store"
        assert (await client.get("/api/v1/auth/me")).json()["termsAccepted"] is True
        user = (await session.execute(select(User).where(User.email == body["email"]))).scalar_one()
        await session.refresh(user)
        assert user.terms_version == TERMS_VERSION
        assert user.terms_accepted_at is not None
    await session.rollback()


@pytest.mark.db
async def test_data_export_lists_the_account_data_without_secrets(session: AsyncSession) -> None:
    body = _account()
    async with _api(session) as client:
        alice = (await client.post("/api/v1/auth/register", json=body)).json()
        session.add_all(
            [
                FollowedMunicipality(user_id=alice["id"], municipality_code="9999997"),
                PushSubscription(
                    id=str(uuid.uuid4()),
                    user_id=alice["id"],
                    endpoint="https://fcm.googleapis.com/fcm/send/segredo-do-aparelho",
                    p256dh="chave-publica",
                    auth="segredo-auth",
                ),
            ]
        )
        await session.flush()
        response = await client.get("/api/v1/me/data")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "attachment" in response.headers["content-disposition"]
        data = response.json()
        assert data["account"]["email"] == body["email"]
        assert data["account"]["termsVersion"] == TERMS_VERSION
        assert data["followedMunicipalities"][0]["municipalityCode"] == "9999997"
        assert data["devices"] == [
            {
                "pushService": "fcm.googleapis.com",
                "registeredAt": data["devices"][0]["registeredAt"],
            }
        ]
        assert data["notifications"] == []
        assert data["activeSessions"] == 1
        for secret in ("segredo-do-aparelho", "segredo-auth", "chave-publica", "scrypt"):
            assert secret not in response.text
    await session.rollback()


@pytest.mark.db
async def test_account_deletion_needs_the_password_and_erases_everything(
    session: AsyncSession,
) -> None:
    body = _account()
    async with _api(session) as client:
        alice = (await client.post("/api/v1/auth/register", json=body)).json()
        session.add_all(
            [
                FollowedMunicipality(user_id=alice["id"], municipality_code="9999997"),
                PushSubscription(
                    id=str(uuid.uuid4()),
                    user_id=alice["id"],
                    endpoint=f"https://fcm.googleapis.com/fcm/send/{uuid.uuid4().hex}",
                    p256dh="chave",
                    auth="auth",
                ),
            ]
        )
        await session.flush()
        wrong = await client.post("/api/v1/me/delete", json={"password": "incorreta"})
        assert wrong.status_code == 400
        assert wrong.json()["error"]["message"] == "Senha incorreta."
        assert (await client.get("/api/v1/auth/me")).status_code == 200

        deleted = await client.post("/api/v1/me/delete", json={"password": body["password"]})
        assert deleted.status_code == 204
        assert f"{security.SESSION_COOKIE}=" in deleted.headers["set-cookie"]
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        for model in (User, UserSession, FollowedMunicipality, PushSubscription):
            column = model.id if model is User else model.user_id
            remaining = await session.execute(select(model).where(column == alice["id"]))
            assert remaining.first() is None
        login = await client.post(
            "/api/v1/auth/login", json={"email": body["email"], "password": body["password"]}
        )
        assert login.status_code == 401
    await session.rollback()


@pytest.mark.db
async def test_account_deletion_is_rate_limited_like_login(session: AsyncSession) -> None:
    async with _api(session) as client:
        await client.post("/api/v1/auth/register", json=_account())
        statuses = [
            (await client.post("/api/v1/me/delete", json={"password": "incorreta"})).status_code
            for _ in range(security.EMAIL_FREE_FAILURES + 3)
        ]
        # A franquia de falhas responde 400 (senha errada); a partir dela o e-mail tranca (429),
        # como no login, porque ambos registram a falha por e-mail.
        blocked = statuses.index(429)
        assert blocked == security.EMAIL_FREE_FAILURES + 1
        assert set(statuses[:blocked]) == {400}
        assert all(status == 429 for status in statuses[blocked:])
    await session.rollback()


async def test_registration_rejects_a_common_password() -> None:
    # Senhas da lista das mais comuns são barradas no cadastro, antes de tocar o banco.
    async with _api() as client:
        response = await client.post("/api/v1/auth/register", json=_account(password="qwerty123"))
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
