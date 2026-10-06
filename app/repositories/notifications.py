from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, exists, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    FollowedMunicipality,
    NotificationEvent,
    PushDelivery,
    PushSubscription,
    WeatherAlert,
)
from app.schemas.notification import PushRegistration


@dataclass(frozen=True, slots=True)
class Candidate:
    user_id: str
    municipality_code: str
    municipality_name: str
    state_abbreviation: str | None
    alert_id: int
    provider: str
    event: str
    severity: str
    description: str | None
    risks: list[str]
    instructions: list[str]


async def candidates(session: AsyncSession) -> list[Candidate]:
    result = await session.execute(
        text("""
        SELECT f.user_id, f.municipality_code, m.name AS municipality_name,
               s.abbreviation AS state_abbreviation, a.id AS alert_id, a.provider::text,
               a.event, a.severity, a.description, a.risks, a.instructions
          FROM followed_municipalities f
          JOIN territories m ON m.ibge_code = f.municipality_code AND m.level = 'municipality'
          LEFT JOIN territories s ON s.id = m.parent_id
          LEFT JOIN territory_geometries g ON g.territory_id = m.id AND g.lod = 'canonical'
          JOIN weather_alerts a ON a.expires > now() AND (
              a.affected_ibge_codes @> jsonb_build_array(f.municipality_code)
              OR (jsonb_array_length(a.affected_ibge_codes) = 0
                  AND ST_Intersects(a.polygon, g.geom))
          )
         WHERE f.notifications_enabled AND f.notifications_opt_in_at IS NOT NULL
    """)
    )
    return [Candidate(**row._asdict()) for row in result]


async def register(session: AsyncSession, user_id: str, body: PushRegistration) -> None:
    # Um endpoint pertence à conta atual; trocar de conta não conserva entregas da anterior.
    await session.execute(
        delete(PushSubscription).where(
            PushSubscription.endpoint == body.endpoint, PushSubscription.user_id != user_id
        )
    )
    await session.execute(
        insert(PushSubscription)
        .values(
            id=str(uuid.uuid4()),
            user_id=user_id,
            endpoint=body.endpoint,
            p256dh=body.keys.p256dh,
            auth=body.keys.auth,
        )
        .on_conflict_do_update(
            index_elements=["endpoint"],
            set_={
                "p256dh": body.keys.p256dh,
                "auth": body.keys.auth,
                "updated_at": func.now(),
            },
        )
    )


async def unregister(session: AsyncSession, user_id: str, endpoint: str) -> None:
    await session.execute(
        delete(PushSubscription).where(
            PushSubscription.user_id == user_id, PushSubscription.endpoint == endpoint
        )
    )


async def enqueue(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        await session.execute(insert(NotificationEvent).values(**row).on_conflict_do_nothing())
    await session.execute(
        text("""
        INSERT INTO push_deliveries (event_id, subscription_id)
        SELECT n.id, s.id FROM notification_events n
          JOIN push_subscriptions s ON s.user_id = n.user_id
          JOIN weather_alerts a ON a.id = n.alert_id AND a.expires > now()
          JOIN followed_municipalities f ON f.user_id = n.user_id
             AND f.municipality_code = n.municipality_code
         WHERE f.notifications_enabled AND f.notifications_opt_in_at IS NOT NULL
        ON CONFLICT DO NOTHING
    """)
    )
    await session.commit()


async def next_pending(
    session: AsyncSession,
) -> tuple[PushDelivery, NotificationEvent, PushSubscription, WeatherAlert] | None:
    subscribed = exists().where(
        FollowedMunicipality.user_id == NotificationEvent.user_id,
        FollowedMunicipality.municipality_code == NotificationEvent.municipality_code,
        FollowedMunicipality.notifications_enabled.is_(True),
        FollowedMunicipality.notifications_opt_in_at.is_not(None),
    )
    row = (
        await session.execute(
            select(PushDelivery, NotificationEvent, PushSubscription, WeatherAlert)
            .join(NotificationEvent, NotificationEvent.id == PushDelivery.event_id)
            .join(PushSubscription, PushSubscription.id == PushDelivery.subscription_id)
            .join(WeatherAlert, WeatherAlert.id == NotificationEvent.alert_id)
            .where(
                PushDelivery.sent_at.is_(None),
                PushDelivery.cancelled_at.is_(None),
                PushDelivery.attempts < 5,
                PushDelivery.next_attempt_at <= func.now(),
                WeatherAlert.expires > func.now(),
                NotificationEvent.user_id == PushSubscription.user_id,
                subscribed,
            )
            .order_by(PushDelivery.created_at, PushDelivery.event_id, PushDelivery.subscription_id)
            .limit(1)
            .with_for_update(of=PushDelivery, skip_locked=True)
        )
    ).first()
    return (row[0], row[1], row[2], row[3]) if row else None


async def lock_dispatch(session: AsyncSession) -> bool:
    return bool(
        (await session.execute(text("SELECT pg_try_advisory_xact_lock(1862743021)"))).scalar()
    )


async def remove_expired_subscription(session: AsyncSession, subscription_id: str) -> None:
    await session.execute(delete(PushSubscription).where(PushSubscription.id == subscription_id))
