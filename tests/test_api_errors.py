from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app import main
from app.api import deps
from app.core.config import settings
from app.main import app


@asynccontextmanager
async def _client(*, session: Any = None) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[Any]:
        yield session

    app.dependency_overrides[deps.get_session] = override
    try:
        # O handler de Exception responde 500 e o Starlette ainda relança o erro ao servidor.
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(deps.get_session, None)


async def test_unknown_route_uses_the_error_envelope() -> None:
    async with _client() as client:
        response = await client.get("/api/v1/nao-existe")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_wrong_method_is_an_http_error_not_a_not_found() -> None:
    async with _client() as client:
        response = await client.post("/api/v1/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "http_error"


async def test_validation_error_names_the_offending_field() -> None:
    async with _client() as client:
        response = await client.get("/api/v1/weather/municipalities", params={"parent": "abc"})

    body = response.json()["error"]
    assert response.status_code == 422
    assert body["code"] == "validation_error"
    assert body["message"] == "Parâmetros inválidos."
    assert body["details"]["fields"][0]["location"] == ["query", "parent"]


async def test_unexpected_failure_is_a_generic_500_that_does_not_leak_the_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import weather

    monkeypatch.setattr(
        weather, "get_sources", AsyncMock(side_effect=RuntimeError("senha=segredo do banco"))
    )
    async with _client() as client:
        response = await client.get("/api/v1/weather/sources")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "segredo" not in response.text


async def test_liveness_does_not_touch_the_database() -> None:
    async with _client(session=object()) as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": settings.app_env}


async def test_readiness_reports_postgis_when_the_database_answers() -> None:
    session = AsyncMock()
    session.execute.return_value = MagicMock(scalar_one=MagicMock(return_value="3.4.2"))
    async with _client(session=session) as client:
        response = await client.get("/api/v1/health/ready")

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["checks"] == {"database": "ok", "postgis": "3.4.2"}


async def test_readiness_is_503_when_the_database_is_down() -> None:
    session = AsyncMock()
    session.execute.side_effect = ConnectionRefusedError("db fora do ar")
    async with _client(session=session) as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "checks": {"database": "unavailable"}}


@asynccontextmanager
async def _session_factory() -> AsyncIterator[Any]:
    yield object()


@pytest.mark.parametrize("hydrography_enabled", [True, False])
async def test_warm_up_survives_a_database_failure_and_warms_hydrography_only_if_enabled(
    monkeypatch: pytest.MonkeyPatch, hydrography_enabled: bool
) -> None:
    monkeypatch.setattr(main, "SessionFactory", _session_factory)
    monkeypatch.setattr(main, "municipality_areas", AsyncMock(side_effect=RuntimeError("db")))
    monkeypatch.setattr(main, "state_areas", AsyncMock())
    monkeypatch.setattr(settings, "hydrography_warmup_enabled", hydrography_enabled)
    hydrography_warm_up = AsyncMock()
    monkeypatch.setattr(main.hydrography, "warm_up", hydrography_warm_up)

    await main._warm_up()

    assert hydrography_warm_up.await_count == int(hydrography_enabled)
