"""Camada meteorológica: estações, observações e alertas — não-territorial.

Três tabelas novas, nenhuma mudança nas existentes. `datasets` e
`ingestion_runs` são reaproveitados como já são (proveniência e frescor por
fonte); ver `app/models/weather.py` para o porquê de não caber em
`indicator_values`.

Escrita à mão pelo mesmo motivo da 0001: ordem de tipos ENUM e índices GiST.

Revision ID: 0004_weather_layer
Revises: 0003_indicator_context
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_weather_layer"
down_revision: str | None = "0003_indicator_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENUM_VALUES: dict[str, tuple[str, ...]] = {
    "weather_provider": ("inmet", "cemaden"),
    "weather_station_type": ("automatic_weather", "rain_gauge"),
}


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*_ENUM_VALUES[name], name=name, create_type=False)


WEATHER_PROVIDER = _enum("weather_provider")
WEATHER_STATION_TYPE = _enum("weather_station_type")


def upgrade() -> None:
    for type_name, values in _ENUM_VALUES.items():
        rendered = ", ".join(f"'{value}'" for value in values)
        op.execute(f"CREATE TYPE {type_name} AS ENUM ({rendered})")

    # -- weather_stations ---------------------------------------------------
    op.create_table(
        "weather_stations",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("provider", WEATHER_PROVIDER, nullable=False),
        sa.Column("external_code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("station_type", WEATHER_STATION_TYPE, nullable=False),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(
                geometry_type="POINT",
                srid=4326,
                spatial_index=False,
                from_text="ST_GeomFromEWKT",
                name="geometry",
            ),
            nullable=False,
        ),
        sa.Column("state_abbreviation", sa.String(length=4), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_weather_stations"),
        sa.UniqueConstraint(
            "provider", "external_code", name="uq_weather_stations_provider_code"
        ),
    )
    op.create_index(
        "ix_weather_stations_geom", "weather_stations", ["geom"], postgresql_using="gist"
    )

    # -- weather_observations -------------------------------------------------
    # PK (station_id, observed_at): mesma idempotência natural que indicator_values.
    op.create_table(
        "weather_observations",
        sa.Column("station_id", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("temperature_c", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("humidity_pct", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("pressure_hpa", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("precipitation_mm", sa.Numeric(precision=7, scale=2), nullable=True),
        sa.Column("dataset_id", sa.SmallInteger(), nullable=False),
        sa.Column("ingestion_run_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["station_id"], ["weather_stations.id"],
            name="fk_weather_observations_station_id_weather_stations", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["datasets.id"],
            name="fk_weather_observations_dataset_id_datasets", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"], ["ingestion_runs.id"],
            name="fk_weather_observations_ingestion_run_id_ingestion_runs", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("station_id", "observed_at", name="pk_weather_observations"),
    )
    op.create_index(
        "ix_weather_observations_station_observed_at",
        "weather_observations",
        ["station_id", "observed_at"],
        postgresql_include=["temperature_c", "humidity_pct", "pressure_hpa", "precipitation_mm"],
    )

    # -- weather_alerts -------------------------------------------------------
    op.create_table(
        "weather_alerts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("provider", WEATHER_PROVIDER, nullable=False),
        sa.Column("external_id", sa.String(length=120), nullable=False),
        sa.Column("event", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=40), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("onset", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "polygon",
            geoalchemy2.types.Geometry(
                geometry_type="MULTIPOLYGON",
                srid=4326,
                spatial_index=False,
                from_text="ST_GeomFromEWKT",
                name="geometry",
            ),
            nullable=False,
        ),
        sa.Column("affected_ibge_codes", postgresql.JSONB(), nullable=False),
        sa.Column("risks", postgresql.JSONB(), nullable=False),
        sa.Column("instructions", postgresql.JSONB(), nullable=False),
        sa.Column("dataset_id", sa.SmallInteger(), nullable=False),
        sa.Column("ingestion_run_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["datasets.id"],
            name="fk_weather_alerts_dataset_id_datasets", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"], ["ingestion_runs.id"],
            name="fk_weather_alerts_ingestion_run_id_ingestion_runs", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_weather_alerts"),
        sa.UniqueConstraint(
            "provider", "external_id", name="uq_weather_alerts_provider_external_id"
        ),
    )
    op.create_index(
        "ix_weather_alerts_polygon", "weather_alerts", ["polygon"], postgresql_using="gist"
    )
    op.create_index("ix_weather_alerts_expires", "weather_alerts", ["expires"])


def downgrade() -> None:
    op.drop_index("ix_weather_alerts_expires", table_name="weather_alerts")
    op.drop_index("ix_weather_alerts_polygon", table_name="weather_alerts", postgresql_using="gist")
    op.drop_table("weather_alerts")

    op.drop_index("ix_weather_observations_station_observed_at", table_name="weather_observations")
    op.drop_table("weather_observations")

    op.drop_index("ix_weather_stations_geom", table_name="weather_stations", postgresql_using="gist")
    op.drop_table("weather_stations")

    for type_name in reversed(list(_ENUM_VALUES)):
        op.execute(f"DROP TYPE IF EXISTS {type_name}")
