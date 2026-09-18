"""Schema inicial: territórios, geometrias, indicadores, valores e ingestão.

Escrita à mão (e não por autogenerate) porque precisa controlar a ordem de três
coisas que o autogenerate não resolve bem: a criação da extensão PostGIS, os
tipos ENUM e o índice GiST/INCLUDE.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Os tipos ENUM são criados explicitamente no início do upgrade e usados nas
# colunas com `create_type=False`. Sem isso, `op.create_table` tenta criar o
# mesmo tipo uma segunda vez e a migration inteira sofre rollback.
_ENUM_VALUES: dict[str, tuple[str, ...]] = {
    "territory_level": ("country", "region", "state", "municipality"),
    "geometry_lod": ("canonical", "overview", "detail"),
    "indicator_origin": ("sourced", "derived"),
    "ingestion_status": ("running", "succeeded", "partial", "failed"),
}


def _enum(name: str) -> postgresql.ENUM:
    """Referência a um tipo ENUM já existente no banco."""
    return postgresql.ENUM(*_ENUM_VALUES[name], name=name, create_type=False)


TERRITORY_LEVEL = _enum("territory_level")
GEOMETRY_LOD = _enum("geometry_lod")
INDICATOR_ORIGIN = _enum("indicator_origin")
INGESTION_STATUS = _enum("ingestion_status")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    for type_name, values in _ENUM_VALUES.items():
        rendered = ", ".join(f"'{value}'" for value in values)
        op.execute(f"CREATE TYPE {type_name} AS ENUM ({rendered})")

    # -- territories ------------------------------------------------------
    # Entidade territorial única: é o que permite a `indicator_values` ter uma
    # FK real em vez de um par (territory_type, territory_id) sem integridade.
    op.create_table(
        "territories",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("level", TERRITORY_LEVEL, nullable=False),
        sa.Column("ibge_code", sa.String(length=9), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("abbreviation", sa.String(length=4), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("capital_territory_id", sa.Integer(), nullable=True),
        sa.Column("bbox_west", sa.Float(), nullable=True),
        sa.Column("bbox_south", sa.Float(), nullable=True),
        sa.Column("bbox_east", sa.Float(), nullable=True),
        sa.Column("bbox_north", sa.Float(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(level = 'country' AND parent_id IS NULL) "
            "OR (level <> 'country' AND parent_id IS NOT NULL)",
            name="country_is_root",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["territories.id"], name="fk_territories_parent_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["capital_territory_id"], ["territories.id"], name="fk_territories_capital_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_territories"),
        sa.UniqueConstraint("ibge_code", name="uq_territories_ibge_code"),
    )
    # Listagens por nível ordenadas por nome (`/territories?level=state`).
    op.create_index("ix_territories_level_name", "territories", ["level", "name"])
    # Filhos de um território: drill-down e contagem de municípios.
    op.create_index("ix_territories_parent_id_name", "territories", ["parent_id", "name"])

    # -- datasets ---------------------------------------------------------
    # Proveniência: fonte, tabela de origem, URL e quando a fonte publicou.
    op.create_table(
        "datasets",
        sa.Column("id", sa.SmallInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=96), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("source_updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_datasets"),
        sa.UniqueConstraint("source", "code", name="uq_datasets_source_code"),
    )

    # -- ingestion_runs ---------------------------------------------------
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("job", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("dataset_code", sa.String(length=96), nullable=True),
        sa.Column("status", INGESTION_STATUS, nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("records_processed", sa.Integer(), nullable=False),
        sa.Column("records_written", sa.Integer(), nullable=False),
        sa.Column("records_failed", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_runs"),
    )
    op.create_index(
        "ix_ingestion_runs_job_started_at",
        "ingestion_runs",
        ["job", sa.text("started_at DESC")],
    )

    # -- territory_geometries --------------------------------------------
    # PK (territory_id, lod): uma geometria por território por nível de detalhe.
    # É essa PK que torna o upsert de geometria idempotente.
    op.create_table(
        "territory_geometries",
        sa.Column("territory_id", sa.Integer(), nullable=False),
        sa.Column("lod", GEOMETRY_LOD, nullable=False),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(
                geometry_type="MULTIPOLYGON",
                srid=4326,
                spatial_index=False,
                from_text="ST_GeomFromEWKT",
                name="geometry",
            ),
            nullable=False,
        ),
        sa.Column("simplify_tolerance", sa.Float(), nullable=True),
        sa.Column("vertex_count", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.SmallInteger(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["territory_id"], ["territories.id"],
            name="fk_territory_geometries_territory_id_territories", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["datasets.id"],
            name="fk_territory_geometries_dataset_id_datasets", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("territory_id", "lod", name="pk_territory_geometries"),
    )
    # Índice espacial: pré-requisito de qualquer predicado geográfico futuro.
    op.create_index(
        "ix_territory_geometries_geom",
        "territory_geometries",
        ["geom"],
        postgresql_using="gist",
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
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_indicators"),
        sa.UniqueConstraint("key", name="uq_indicators_key"),
    )

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
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "reference_year BETWEEN 1900 AND 2100",
            name="reference_year_range",
        ),
        sa.ForeignKeyConstraint(
            ["territory_id"], ["territories.id"],
            name="fk_indicator_values_territory_id_territories", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["indicator_id"], ["indicators.id"],
            name="fk_indicator_values_indicator_id_indicators", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["datasets.id"],
            name="fk_indicator_values_dataset_id_datasets", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"], ["ingestion_runs.id"],
            name="fk_indicator_values_ingestion_run_id_ingestion_runs", ondelete="SET NULL",
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


def downgrade() -> None:
    op.drop_index("ix_indicator_values_dataset_id", table_name="indicator_values")
    op.drop_index(
        "ix_indicator_values_indicator_id_reference_year", table_name="indicator_values"
    )
    op.drop_table("indicator_values")
    op.drop_table("indicators")
    op.drop_index(
        "ix_territory_geometries_geom",
        table_name="territory_geometries",
        postgresql_using="gist",
    )
    op.drop_table("territory_geometries")
    op.drop_index("ix_ingestion_runs_job_started_at", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")
    op.drop_table("datasets")
    op.drop_index("ix_territories_parent_id_name", table_name="territories")
    op.drop_index("ix_territories_level_name", table_name="territories")
    op.drop_table("territories")

    for type_name in reversed(list(_ENUM_VALUES)):
        op.execute(f"DROP TYPE IF EXISTS {type_name}")
