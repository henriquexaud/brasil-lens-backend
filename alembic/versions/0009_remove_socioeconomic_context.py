"""Remove o domínio estatístico e preserva geografia e dados ambientais.

A exclusão de dados é definitiva: downgrade restaura apenas o schema anterior.
Para recuperar os dados removidos, é necessário restaurar um backup.
Migrations anteriores permanecem imutáveis para permitir upgrades existentes.

Revision ID: 0009_remove_socioeconomic
Revises: 0008_followed_notifications
Create Date: 2026-09-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_remove_socioeconomic"
down_revision: str | None = "0008_followed_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Types are created explicitly, so table creation must not create them again.
INDICATOR_ORIGIN = postgresql.ENUM("sourced", "derived", name="indicator_origin", create_type=False)
DATA_CONTEXT = postgresql.ENUM(
    "sociopolitical",
    "climate_environmental",
    "biodiversity",
    name="data_context",
    create_type=False,
)
TERRITORY_LEVEL = postgresql.ENUM(
    "country", "region", "state", "municipality", name="territory_level", create_type=False
)


def upgrade() -> None:
    # No CASCADE: unexpected dependencies must abort the transaction, not be removed.
    op.drop_table("saved_views")
    op.drop_table("indicator_values")
    op.drop_table("indicators")
    op.execute("DROP TYPE data_context")
    op.execute("DROP TYPE indicator_origin")

    # Remove obsolete provenance only when no remaining environmental/geographic
    # data references it. Geography and weather keep their original provenance.
    op.execute(
        """
        DELETE FROM datasets d
         WHERE ((d.source = 'ibge' AND d.code LIKE 'agregados/%')
             OR (d.source = 'brasil-lens' AND d.code LIKE 'derived/%'))
           AND NOT EXISTS (SELECT 1 FROM territory_geometries g WHERE g.dataset_id = d.id)
           AND NOT EXISTS (SELECT 1 FROM weather_observations w WHERE w.dataset_id = d.id)
           AND NOT EXISTS (SELECT 1 FROM weather_alerts a WHERE a.dataset_id = d.id)
        """
    )
    op.execute(
        """
        DELETE FROM ingestion_runs r
         WHERE r.job IN ('seed_indicators', 'import_indicators')
           AND NOT EXISTS (SELECT 1 FROM weather_observations w WHERE w.ingestion_run_id = r.id)
           AND NOT EXISTS (SELECT 1 FROM weather_alerts a WHERE a.ingestion_run_id = r.id)
        """
    )


def downgrade() -> None:
    # Restore the exact schema at 0008; deleted values/provenance require a backup.
    op.execute("CREATE TYPE indicator_origin AS ENUM ('sourced', 'derived')")
    op.execute(
        "CREATE TYPE data_context AS ENUM "
        "('sociopolitical', 'climate_environmental', 'biodiversity')"
    )
    # -- indicators -------------------------------------------------------
    op.create_table(
        "indicators",
        sa.Column("id", sa.SmallInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(length=24), nullable=False),
        sa.Column("origin", INDICATOR_ORIGIN, nullable=False),
        sa.Column("decimal_places", sa.SmallInteger(), nullable=False),
        sa.Column("display_order", sa.SmallInteger(), nullable=False),
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
        sa.Column("context", DATA_CONTEXT, nullable=False, server_default="sociopolitical"),
        sa.PrimaryKeyConstraint("id", name="pk_indicators"),
        sa.UniqueConstraint("key", name="uq_indicators_key"),
    )

    op.create_index("ix_indicators_context", "indicators", ["context"])

    # -- indicator_values -------------------------------------------------
    # PK composta = chave natural do domínio (território ↔ indicador ↔ ano).
    # Serve simultaneamente como constraint de unicidade para o upsert e como
    # índice do overview (WHERE territory_id = ?).
    op.create_table(
        "indicator_values",
        sa.Column("territory_id", sa.Integer(), nullable=False),
        sa.Column("indicator_id", sa.SmallInteger(), nullable=False),
        sa.Column("reference_year", sa.SmallInteger(), nullable=False),
        sa.Column("value", sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column("dataset_id", sa.SmallInteger(), nullable=False),
        sa.Column("ingestion_run_id", sa.BigInteger(), nullable=True),
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
            "reference_year BETWEEN 1900 AND 2100",
            name="reference_year_range",
        ),
        sa.ForeignKeyConstraint(
            ["territory_id"],
            ["territories.id"],
            name="fk_indicator_values_territory_id_territories",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["indicator_id"],
            ["indicators.id"],
            name="fk_indicator_values_indicator_id_indicators",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_indicator_values_dataset_id_datasets",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_runs.id"],
            name="fk_indicator_values_ingestion_run_id_ingestion_runs",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "territory_id", "indicator_id", "reference_year", name="pk_indicator_values"
        ),
    )
    # Query do mapa: filtra indicador + ano e precisa de território e valor.
    # O INCLUDE deixa a leitura index-only (sem tocar a heap).
    op.create_index(
        "ix_indicator_values_indicator_id_reference_year",
        "indicator_values",
        ["indicator_id", "reference_year"],
        postgresql_include=["territory_id", "value"],
    )
    op.create_index("ix_indicator_values_dataset_id", "indicator_values", ["dataset_id"])

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
