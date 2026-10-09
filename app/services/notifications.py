from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.models import NotificationEvent, WeatherAlert
from app.providers import push
from app.repositories import notifications as repo
from app.schemas.notification import PushRegistration
from app.services.weather import _severity_level_for

logger = get_logger(__name__)


async def register_device(session: AsyncSession, user_id: str, body: PushRegistration) -> None:
    await repo.register(session, user_id, body)


async def unregister_device(session: AsyncSession, user_id: str, endpoint: str) -> None:
    await repo.unregister(session, user_id, endpoint)


def version(alert: repo.Candidate | WeatherAlert) -> str:
    # Renovar a validade artificial do CEMADEN não significa novo aviso.
    return hashlib.sha256(
        orjson.dumps(
            [alert.event, alert.severity, alert.description, alert.risks, alert.instructions]
        )
    ).hexdigest()


def content(candidate: repo.Candidate) -> dict[str, Any]:
    # Evento de domínio, independente da formatação e do canal de entrega.
    return {
        "municipalityCode": candidate.municipality_code,
        "municipalityName": candidate.municipality_name,
        "stateAbbreviation": candidate.state_abbreviation,
        "source": candidate.provider,
        "event": candidate.event,
        "severity": candidate.severity,
        "description": candidate.description,
        "risks": candidate.risks,
        "instructions": candidate.instructions,
    }


def push_payload(event: NotificationEvent, alert: WeatherAlert) -> dict[str, Any]:
    data = event.content
    city = data["municipalityName"]
    if data["stateAbbreviation"]:
        city += f" ({data['stateAbbreviation']})"
    severity = _severity_level_for(data["source"], data["severity"]).value
    return {
        "title": f"Brasil Lens · {city}",
        "body": f"{data['event']} · {data['severity']} · {data['source'].upper()}",
        "tag": f"brasil-lens-{event.id}",
        "data": {
            "municipalityCode": event.municipality_code,
            "url": f"/?municipality={event.municipality_code}",
            "expiresAt": alert.expires.isoformat(),
            "severityLevel": severity,
        },
    }


async def dispatch(session: AsyncSession) -> int:
    candidates = await repo.candidates(session)
    eligible = {(item.user_id, item.alert_id, item.municipality_code) for item in candidates}
    await repo.enqueue(
        session,
        [
            {
                "id": str(uuid.uuid4()),
                "user_id": item.user_id,
                "alert_id": item.alert_id,
                "municipality_code": item.municipality_code,
                "alert_version": version(item),
                "content": content(item),
            }
            for item in candidates
        ],
    )
    sent = 0
    for _ in range(settings.notification_batch_limit):
        stop = False
        async with session.begin():
            if not await repo.lock_dispatch(session):
                return sent
            pending = await repo.next_pending(session)
            if pending is None:
                return sent
            delivery, event, subscription, alert = pending
            now = datetime.now(UTC)
            if (
                event.alert_version != version(alert)
                or (event.user_id, event.alert_id, event.municipality_code) not in eligible
            ):
                delivery.cancelled_at = now
                continue
            ttl = max(0, min(3600, int((alert.expires - now).total_seconds())))
            if ttl == 0:
                delivery.cancelled_at = now
                continue
            delivery.attempts += 1
            try:
                await push.send(
                    {
                        "endpoint": subscription.endpoint,
                        "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                    },
                    push_payload(event, alert),
                    ttl,
                    event.id.replace("-", ""),
                )
            except ProviderError as exc:
                status = (exc.details or {}).get("status")
                if status in {404, 410}:
                    # A exclusão em cascata apaga a entrega; grave a tentativa antes disso.
                    await session.flush()
                    await repo.remove_expired_subscription(session, subscription.id)
                else:
                    delivery.last_error = exc.message
                    delivery.next_attempt_at = now + timedelta(
                        seconds=min(3600, 600 * 2 ** (delivery.attempts - 1))
                    )
                    if status == 400:
                        delivery.attempts = 5
                    if status in {401, 403, 429} or (exc.details or {}).get("configuration"):
                        delivery.attempts -= 1
                        stop = True
                logger.warning(
                    "notifications.send_failed", extra={"event_id": event.id, "status": status}
                )
            else:
                delivery.sent_at = now
                delivery.last_error = None
                sent += 1
        if stop:
            break
    return sent
