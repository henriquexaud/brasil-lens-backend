"""Notificações por município seguido.

Uma coluna: se o vínculo `usuário ↔ município` deve gerar notificações. Ligada
por padrão — seguir já é o gesto de "quero acompanhar isto" — e desligável
sem deixar de seguir. Nenhuma regra de disparo ainda mora aqui.

Revision ID: 0008_followed_notifications
Revises: 0007_followed_municipalities
Create Date: 2026-09-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_followed_notifications"
down_revision: str | None = "0007_followed_municipalities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "followed_municipalities",
        sa.Column(
            "notifications_enabled",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("followed_municipalities", "notifications_enabled")
