"""Busca textual de territórios sem acentos e com indexação.

Adiciona a extensão unaccent do PostgreSQL e colunas pré-normalizadas
(normalized_name e normalized_abbreviation) na tabela territories,
com índices dedicados para busca rápida e insensível a acentos e caixa.

Revision ID: 0005_territory_search
Revises: 0004_weather_layer
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_territory_search"
down_revision: str | None = "0004_weather_layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Habilita unaccent para permitir normalizações SQL no PostgreSQL
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent;")

    # 2. Adiciona colunas normalizadas para indexação B-Tree
    op.add_column(
        "territories",
        sa.Column("normalized_name", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "territories",
        sa.Column("normalized_abbreviation", sa.String(length=4), nullable=True),
    )

    # 3. Popula os valores das colunas existentes
    op.execute(
        """
        UPDATE territories
           SET normalized_name = lower(unaccent(name)),
               normalized_abbreviation = lower(unaccent(abbreviation));
        """
    )

    # 4. Cria índices para busca e ordenação rápida
    op.create_index(
        "ix_territories_normalized_name",
        "territories",
        ["normalized_name"],
    )
    op.create_index(
        "ix_territories_normalized_abbr",
        "territories",
        ["normalized_abbreviation"],
    )


def downgrade() -> None:
    op.drop_index("ix_territories_normalized_abbr", table_name="territories")
    op.drop_index("ix_territories_normalized_name", table_name="territories")
    op.drop_column("territories", "normalized_abbreviation")
    op.drop_column("territories", "normalized_name")

