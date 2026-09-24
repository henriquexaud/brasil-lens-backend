"""Municípios seguidos — a relação `usuário ↔ município` do contexto Clima.

Só a relação: nenhuma coluna de alerta, canal ou dado meteorológico. Ver
`app/models/followed_municipality.py` para o porquê de `user_id` opaco e de o
município ir por código IBGE sem FK.

Revision ID: 0007_followed_municipalities
Revises: 0006_weather_alert_description
Create Date: 2026-09-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_followed_municipalities"
down_revision: str | None = "0006_weather_alert_description"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "followed_municipalities",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("municipality_code", sa.String(length=7), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "municipality_code ~ '^[0-9]{7}$'",
            name="ck_followed_municipalities_municipality_code_format",
        ),
        sa.CheckConstraint(
            "length(btrim(user_id)) > 0",
            name="ck_followed_municipalities_user_id_not_blank",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_followed_municipalities"),
        # Um usuário segue um município no máximo uma vez.
        sa.UniqueConstraint(
            "user_id",
            "municipality_code",
            name="uq_followed_municipalities_user_id_municipality_code",
        ),
    )
    # "Quem segue este município?" — a consulta de um futuro disparador de alertas.
    op.create_index(
        "ix_followed_municipalities_municipality_code",
        "followed_municipalities",
        ["municipality_code"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_followed_municipalities_municipality_code",
        table_name="followed_municipalities",
    )
    op.drop_table("followed_municipalities")
