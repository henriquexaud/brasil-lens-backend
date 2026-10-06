from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_notifications"
down_revision: str | None = "0010_user_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(name, sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now())
        for name in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    op.add_column(
        "followed_municipalities", sa.Column("notifications_opt_in_at", sa.TIMESTAMP(timezone=True))
    )
    op.alter_column("followed_municipalities", "notifications_enabled", server_default=sa.false())
    op.execute("UPDATE followed_municipalities SET notifications_enabled = false")
    op.create_table(
        "notification_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "alert_id",
            sa.BigInteger(),
            sa.ForeignKey("weather_alerts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("municipality_code", sa.String(7), nullable=False),
        sa.Column("alert_version", sa.String(64), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("user_id", "alert_id", "municipality_code", "alert_version"),
    )
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("endpoint", sa.Text(), nullable=False, unique=True),
        sa.Column("p256dh", sa.String(100), nullable=False),
        sa.Column("auth", sa.String(30), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_push_subscriptions_user_id", "push_subscriptions", ["user_id"])
    op.create_table(
        "push_deliveries",
        sa.Column(
            "event_id",
            sa.String(36),
            sa.ForeignKey("notification_events.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "subscription_id",
            sa.String(36),
            sa.ForeignKey("push_subscriptions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "next_attempt_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("cancelled_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_error", sa.String(200)),
        *timestamps(),
    )
    op.create_index("ix_push_deliveries_pending", "push_deliveries", ["next_attempt_at"])


def downgrade() -> None:
    op.drop_table("push_deliveries")
    op.drop_table("push_subscriptions")
    op.drop_table("notification_events")
    op.drop_column("followed_municipalities", "notifications_opt_in_at")
    op.alter_column("followed_municipalities", "notifications_enabled", server_default=sa.true())
