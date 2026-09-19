"""Contexto de domínio dos indicadores.

Adiciona o agrupamento temático (sociopolítico, clima/ambiente, biodiversidade)
que o futuro frontend usará para alternar entre contextos de dados em vez de
carregar tudo de uma vez (ver docs/ARCHITECTURE.md). Todo indicador existente é
sociopolítico, então o `server_default` faz o backfill sem UPDATE explícito.

Revision ID: 0003_indicator_context
Revises: 0002_saved_views
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_indicator_context"
down_revision: str | None = "0002_saved_views"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DATA_CONTEXT_VALUES: tuple[str, ...] = (
    "sociopolitical",
    "climate_environmental",
    "biodiversity",
)

# Tipo já criado explicitamente no upgrade; create_type=False evita que
# op.add_column tente criá-lo de novo (mesma técnica da 0001).
DATA_CONTEXT = postgresql.ENUM(*DATA_CONTEXT_VALUES, name="data_context", create_type=False)


def upgrade() -> None:
    rendered = ", ".join(f"'{value}'" for value in DATA_CONTEXT_VALUES)
    op.execute(f"CREATE TYPE data_context AS ENUM ({rendered})")

    op.add_column(
        "indicators",
        sa.Column(
            "context",
            DATA_CONTEXT,
            nullable=False,
            server_default="sociopolitical",
        ),
    )
    # Filtro por contexto no catálogo (`/indicators?context=`).
    op.create_index("ix_indicators_context", "indicators", ["context"])


def downgrade() -> None:
    op.drop_index("ix_indicators_context", table_name="indicators")
    op.drop_column("indicators", "context")
    op.execute("DROP TYPE IF EXISTS data_context")
