"""Visualizações salvas pelo usuário.

Escrita à mão, como a 0001, por dois motivos: o ENUM `territory_level` já existe
no banco e precisa ser referenciado com `create_type=False` (senão o
`op.create_table` tenta criá-lo de novo e a migration inteira sofre rollback), e
o default de `public_id` é gerado pela aplicação, não pelo banco.

Revision ID: 0002_saved_views
Revises: 0001_initial_schema
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_saved_views"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TERRITORY_LEVEL = postgresql.ENUM(
    "country",
    "region",
    "state",
    "municipality",
    name="territory_level",
    create_type=False,
)


def upgrade() -> None:
    op.create_table(
        "saved_views",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("public_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("level", TERRITORY_LEVEL, nullable=False),
        sa.Column("parent_code", sa.String(length=9), nullable=True),
        sa.Column("indicator_key", sa.String(length=64), nullable=False),
        sa.Column("reference_year", sa.SmallInteger(), nullable=True),
        sa.Column("classes", sa.SmallInteger(), nullable=False, server_default=sa.text("5")),
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
        # NULL em reference_year é "último ano disponível" — o mesmo `latest`
        # da rota do mapa.
        sa.CheckConstraint(
            "reference_year IS NULL OR reference_year BETWEEN 1900 AND 2100",
            name="ck_saved_views_saved_view_year_range",
        ),
        sa.CheckConstraint(
            "classes BETWEEN 2 AND 9",
            name="ck_saved_views_saved_view_class_range",
        ),
        # A mesma regra que a rota /map aplica, agora garantida pelo banco:
        # nenhuma visualização impossível de abrir pode ser gravada.
        sa.CheckConstraint(
            "level <> 'municipality' OR parent_code IS NOT NULL",
            name="ck_saved_views_saved_view_municipality_needs_parent",
        ),
        sa.CheckConstraint(
            "length(btrim(name)) > 0",
            name="ck_saved_views_saved_view_name_not_blank",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_saved_views"),
        sa.UniqueConstraint("public_id", name="uq_saved_views_public_id"),
        sa.UniqueConstraint("name", name="uq_saved_views_name"),
    )
    # A listagem é sempre "mais recentes primeiro".
    op.create_index("ix_saved_views_created_at", "saved_views", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_saved_views_created_at", table_name="saved_views")
    op.drop_table("saved_views")
