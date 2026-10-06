from __future__ import annotations

import base64
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError
from pywebpush import WebPushException
from requests import Response
from requests.exceptions import Timeout
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ProviderError
from app.jobs import dispatch_notifications as job
from app.models import (
    Dataset,
    FollowedMunicipality,
    GeometryLOD,
    NotificationEvent,
    PushDelivery,
    PushSubscription,
    Territory,
    TerritoryGeometry,
    TerritoryLevel,
    User,
    WeatherAlert,
    WeatherProvider,
)
from app.providers import push
from app.repositories import followed_municipalities as follows
from app.repositories import notifications as repo
from app.schemas.notification import PushEndpoint, PushRegistration
from app.services import notifications as service


def encoded(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@pytest_asyncio.fixture
async def session(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(
        bind=await session.connection(),
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as isolated:
        yield isolated
    await session.rollback()


@pytest.fixture(autouse=True)
def sender(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    monkeypatch.setattr(settings, "vapid_private_key", SecretStr("test-secret"))
    monkeypatch.setattr(settings, "vapid_public_key", "test-public")
    mock = MagicMock()
    monkeypatch.setattr(push, "webpush", mock)
    return mock


@pytest.fixture
def registration() -> PushRegistration:
    public = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    return PushRegistration(
        endpoint="https://fcm.googleapis.com/fcm/send/test",
        keys={"p256dh": encoded(public), "auth": encoded(b"a" * 16)},
    )


@pytest_asyncio.fixture
async def scenario(session: AsyncSession, registration: PushRegistration) -> dict[str, object]:
    token = uuid.uuid4().hex[:10]
    root = Territory(level=TerritoryLevel.COUNTRY, ibge_code=token[:9], name="Teste")
    dataset = Dataset(source="notification-test", code=token, name="Avisos de teste")
    user = User(
        id=token, name="Conta de teste", email=f"{token}@example.com", password_hash="fixture"
    )
    session.add_all([root, dataset, user])
    await session.flush()
    city = Territory(
        level=TerritoryLevel.MUNICIPALITY, ibge_code="9000001", name="Cidade A", parent_id=root.id
    )
    other = Territory(
        level=TerritoryLevel.MUNICIPALITY, ibge_code="9000002", name="Cidade B", parent_id=root.id
    )
    session.add_all([city, other])
    await session.flush()
    for territory, west in [(city, -46), (other, -43)]:
        session.add(
            TerritoryGeometry(
                territory_id=territory.id,
                lod=GeometryLOD.CANONICAL,
                geom=func.ST_Multi(func.ST_MakeEnvelope(west, -22, west + 1, -21, 4326)),
                vertex_count=5,
            )
        )
    follow = FollowedMunicipality(
        user_id=user.id,
        municipality_code=city.ibge_code,
        notifications_enabled=True,
        notifications_opt_in_at=datetime.now(UTC),
    )
    alert = WeatherAlert(
        provider=WeatherProvider.CEMADEN,
        external_id=token,
        event="Risco hidrológico",
        severity="Moderado",
        onset=datetime.now(UTC) - timedelta(minutes=5),
        expires=datetime.now(UTC) + timedelta(hours=2),
        polygon=func.ST_Multi(func.ST_MakeEnvelope(-46, -22, -45, -21, 4326)),
        affected_ibge_codes=[city.ibge_code],
        risks=[],
        instructions=[],
        dataset_id=dataset.id,
    )
    session.add_all([follow, alert])
    await repo.register(session, user.id, registration)
    await session.commit()
    return {"user": user, "follow": follow, "alert": alert, "city": city, "other": other}


@pytest.mark.db
async def test_event_is_channel_independent_and_validity_refresh_does_not_repeat_push(
    session, scenario, sender
):
    assert await service.dispatch(session) == 1
    scenario["alert"].expires += timedelta(minutes=30)
    await session.commit()
    assert await service.dispatch(session) == 0
    sender.assert_called_once()
    payload = json.loads(sender.call_args.kwargs["data"])
    assert "Cidade A" in payload["title"]
    assert payload["data"]["url"] == "/?municipality=9000001"
    assert "CEMADEN" in payload["body"]
    assert 0 < sender.call_args.kwargs["ttl"] <= 3600
    event = (await session.scalars(select(NotificationEvent))).one()
    assert "email" not in event.content and "userName" not in event.content
    assert event.content["event"] == "Risco hidrológico"
    delivery = (await session.scalars(select(PushDelivery))).one()
    assert delivery.sent_at is not None


@pytest.mark.db
async def test_changed_severity_generates_a_new_event(session, scenario, sender):
    assert await service.dispatch(session) == 1
    scenario["alert"].severity = "Alto"
    await session.commit()
    assert await service.dispatch(session) == 1
    assert sender.call_count == 2
    assert "Alto" in json.loads(sender.call_args.kwargs["data"])["body"]


@pytest.mark.db
@pytest.mark.parametrize("reason", ["off", "no_consent", "expired", "not_affected"])
async def test_push_requires_current_authorization_and_applicable_alert(
    session, scenario, sender, reason
):
    if reason == "off":
        scenario["follow"].notifications_enabled = False
    elif reason == "no_consent":
        scenario["follow"].notifications_opt_in_at = None
    elif reason == "expired":
        scenario["alert"].expires = datetime.now(UTC) - timedelta(minutes=1)
    else:
        scenario["alert"].affected_ibge_codes = [scenario["other"].ibge_code]
    await session.commit()
    assert await service.dispatch(session) == 0
    sender.assert_not_called()


@pytest.mark.db
async def test_inmet_polygon_uses_canonical_geometry_not_other_followed_city(session, scenario):
    scenario["alert"].provider = WeatherProvider.INMET
    scenario["alert"].affected_ibge_codes = []
    session.add(
        FollowedMunicipality(
            user_id=scenario["user"].id,
            municipality_code=scenario["other"].ibge_code,
            notifications_enabled=True,
            notifications_opt_in_at=datetime.now(UTC),
        )
    )
    await session.commit()
    assert [row.municipality_code for row in await repo.candidates(session)] == ["9000001"]


@pytest.mark.db
async def test_all_devices_receive_event_once_and_new_device_receives_active_notice(
    session, scenario, registration, sender
):
    assert await service.dispatch(session) == 1
    await repo.register(
        session,
        scenario["user"].id,
        registration.model_copy(update={"endpoint": registration.endpoint + "-second"}),
    )
    await session.commit()
    assert await service.dispatch(session) == 1
    assert await service.dispatch(session) == 0
    assert sender.call_count == 2


@pytest.mark.db
async def test_retry_retains_event_tag_and_topic(session, scenario, sender):
    sender.side_effect = Timeout("secret-endpoint")
    assert await service.dispatch(session) == 0
    first = sender.call_args.kwargs.copy()
    delivery = (await session.scalars(select(PushDelivery))).one()
    assert delivery.attempts == 1
    assert "secret" not in delivery.last_error
    await session.execute(update(PushDelivery).values(next_attempt_at=func.now()))
    await session.commit()
    sender.side_effect = None
    assert await service.dispatch(session) == 1
    assert first["data"] == sender.call_args.kwargs["data"]
    assert first["headers"]["Topic"] == sender.call_args.kwargs["headers"]["Topic"]


@pytest.mark.db
@pytest.mark.parametrize("reason", ["disable", "unfollow", "area_changed"])
async def test_pending_delivery_stops_after_revocation_or_area_change(
    session, scenario, sender, reason
):
    sender.side_effect = Timeout("network")
    assert await service.dispatch(session) == 0
    await session.execute(update(PushDelivery).values(next_attempt_at=func.now()))
    if reason == "disable":
        await follows.set_notifications(session, scenario["user"].id, "9000001", False)
    elif reason == "unfollow":
        await follows.unfollow(session, scenario["user"].id, "9000001")
    else:
        scenario["alert"].affected_ibge_codes = ["9000002"]
    await session.commit()
    sender.side_effect = None
    assert await service.dispatch(session) == 0
    assert sender.call_count == 1


@pytest.mark.db
@pytest.mark.parametrize("status", [404, 410])
async def test_expired_push_endpoint_is_removed(session, scenario, sender, status):
    response = Response()
    response.status_code = status
    sender.side_effect = WebPushException("private endpoint", response=response)
    assert await service.dispatch(session) == 0
    assert (await session.scalars(select(PushSubscription))).all() == []
    assert (await session.scalars(select(PushDelivery))).all() == []


@pytest.mark.db
@pytest.mark.parametrize("status", [401, 403, 429, None])
async def test_global_push_failure_preserves_deliveries_until_configuration_is_fixed(
    session, scenario, sender, status
):
    original = (await session.scalars(select(PushSubscription))).one()
    session.add(
        PushSubscription(
            id=str(uuid.uuid4()),
            user_id=original.user_id,
            endpoint=original.endpoint + "-second",
            p256dh=original.p256dh,
            auth=original.auth,
        )
    )
    await session.flush()
    if status is None:
        sender.side_effect = ValueError("private-key-in-error")
    else:
        response = Response()
        response.status_code = status
        sender.side_effect = WebPushException("private-provider-response", response=response)
    assert await service.dispatch(session) == 0
    assert sender.call_count == 1
    deliveries = (await session.scalars(select(PushDelivery))).all()
    assert len(deliveries) == 2
    assert all(item.attempts == 0 and item.sent_at is None for item in deliveries)
    assert all("private" not in (item.last_error or "") for item in deliveries)
    sender.side_effect = None
    await session.execute(update(PushDelivery).values(next_attempt_at=func.now()))
    await session.commit()
    assert await service.dispatch(session) == 2


@pytest.mark.db
async def test_device_registration_is_idempotent_and_cannot_unsubscribe_other_account(
    session, scenario, registration
):
    await repo.register(session, scenario["user"].id, registration)
    await repo.unregister(session, "another-user", registration.endpoint)
    assert len((await session.scalars(select(PushSubscription))).all()) == 1
    other = User(id="another-user", name="Outra conta")
    session.add(other)
    await session.flush()
    await repo.register(session, other.id, registration)
    subscription = (await session.scalars(select(PushSubscription))).one()
    assert subscription.user_id == other.id
    await repo.unregister(session, scenario["user"].id, registration.endpoint)
    assert len((await session.scalars(select(PushSubscription))).all()) == 1


@pytest.mark.db
async def test_http_config_and_subscription_contract(session, scenario, registration):
    from app.api.deps import get_current_user_id, get_session, get_write_session
    from app.main import app

    async def override_session():
        yield session

    async def override_user():
        return scenario["user"].id

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
            response = await client.get("/api/v1/me/notifications/config")
            assert response.json() == {"publicKey": "test-public"}
            assert response.headers["cache-control"] == "no-store"
            assert (
                await client.post(
                    "/api/v1/me/notifications/subscriptions", json=registration.model_dump()
                )
            ).status_code == 204
            assert (
                await client.post(
                    "/api/v1/me/notifications/unsubscribe", json={"endpoint": registration.endpoint}
                )
            ).status_code == 204
            assert (await session.scalars(select(PushSubscription))).all() == []
            assert (
                await client.post(
                    "/api/v1/me/notifications/subscriptions",
                    json={**registration.model_dump(), "endpoint": "https://127.0.0.1/internal"},
                )
            ).status_code == 422
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/a",
        "https://127.0.0.1/a",
        "https://fcm.googleapis.com.evil.test/a",
        "https://user@fcm.googleapis.com/a",
        "https://fcm.googleapis.com:8000/a",
        "https://evil.test/a",
    ],
)
def test_only_official_https_push_hosts_are_accepted(endpoint):
    with pytest.raises(ValidationError):
        PushEndpoint(endpoint=endpoint)


def test_subscription_rejects_invalid_encryption_keys(registration):
    with pytest.raises(ValidationError):
        PushRegistration(endpoint=registration.endpoint, keys={"p256dh": "AAAA", "auth": "AA"})


async def test_adapter_uses_vapid_and_does_not_log_provider_response(sender, registration):
    await push.send(registration.model_dump(), {"title": "Teste"}, 120, "topic")
    assert sender.call_args.kwargs["vapid_private_key"] == "test-secret"
    assert sender.call_args.kwargs["timeout"] == 10
    response = Response()
    response.status_code = 403
    sender.side_effect = WebPushException("private-url-and-key", response=response)
    with pytest.raises(ProviderError) as caught:
        await push.send(registration.model_dump(), {"title": "Teste"}, 120, "topic")
    assert caught.value.details == {"status": 403}
    assert "private" not in caught.value.message


async def test_disabled_job_never_opens_database(monkeypatch):
    monkeypatch.setattr(settings, "push_notifications_enabled", False)
    assert await job.main() == 0
    monkeypatch.setattr(settings, "push_notifications_enabled", True)
    monkeypatch.setattr(settings, "vapid_private_key", None)
    assert await job.main() == 1
