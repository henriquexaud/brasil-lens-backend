"""A remoção do domínio antigo deve preservar a base geográfica e ambiental."""

import pytest
from sqlalchemy import inspect, text

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from app.db.base import Base
from app.main import app


def migration_script():
    return ScriptDirectory.from_config(Config("alembic.ini"))


def test_revisions_fit_the_alembic_version_column():
    assert all(len(revision.revision) <= 32 for revision in migration_script().walk_revisions())


def test_public_api_exposes_only_geography_and_environment():
    paths = app.openapi()["paths"]
    assert not ({"/api/v1/contexts", "/api/v1/indicators", "/api/v1/views"} & paths.keys())
    assert "/api/v1/map/values" not in paths
    assert "/api/v1/territories/{ibge_code}/overview" not in paths
    assert "/api/v1/territories/{ibge_code}/indicators" not in paths
    parameters = {item["name"] for item in paths["/api/v1/map"]["get"]["parameters"]}
    assert parameters == {"level", "parent", "lod"}


@pytest.mark.db
async def test_migration_removes_exclusive_data_and_preserves_shared_references(session):
    migration = migration_script().get_revision("0009_remove_socioeconomic").module

    def migrate(sync_session, direction):
        context = MigrationContext.configure(
            sync_session.connection(), opts={"target_metadata": Base.metadata}
        )
        with Operations.context(context):
            getattr(migration, direction)()

    try:
        # Restore the old schema inside this transaction to exercise both directions.
        await session.run_sync(migrate, "downgrade")
        indexes = await session.run_sync(
            lambda s: inspect(s.connection()).get_indexes("indicator_values")
        )
        assert {i["name"] for i in indexes} >= {
            "ix_indicator_values_dataset_id",
            "ix_indicator_values_indicator_id_reference_year",
        }
        labels = (
            (
                await session.execute(
                    text("""
            SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
             WHERE t.typname = 'data_context' ORDER BY enumsortorder
        """)
                )
            )
            .scalars()
            .all()
        )
        assert labels == ["sociopolitical", "climate_environmental", "biodiversity"]

        datasets = {}
        for source, code in [
            ("ibge", "agregados/review"),
            ("brasil-lens", "derived/review"),
            ("ibge", "malhas/review"),
            ("inmet", "review/stations"),
            ("brasil-lens", "derived/review-shared"),
        ]:
            datasets[code] = (
                await session.execute(
                    text("""
                INSERT INTO datasets (source, code, name)
                VALUES (:source, :code, 'Migration review') RETURNING id
            """),
                    {"source": source, "code": code},
                )
            ).scalar_one()
        runs = {}
        for job in ["seed_indicators", "import_indicators", "import_geometries"]:
            runs[job] = (
                await session.execute(
                    text("""
                INSERT INTO ingestion_runs
                    (job, source, status, records_processed, records_written, records_failed)
                VALUES (:job, 'review', 'succeeded', 1, 1, 0) RETURNING id
            """),
                    {"job": job},
                )
            ).scalar_one()
        territory = (
            await session.execute(
                text("""
            INSERT INTO territories (level, ibge_code, name)
            VALUES ('country', 'MIGR', 'Migration review') RETURNING id
        """)
            )
        ).scalar_one()
        await session.execute(
            text("""
            INSERT INTO territory_geometries (territory_id, lod, geom, vertex_count, dataset_id)
            VALUES (:id, 'canonical', ST_Multi(ST_MakeEnvelope(-46, -22, -45, -21, 4326)), 5, :d)
        """),
            {"id": territory, "d": datasets["derived/review-shared"]},
        )
        station = (
            await session.execute(
                text("""
            INSERT INTO weather_stations (provider, external_code, name, station_type, geom)
            VALUES ('inmet', 'REVIEW', 'Review', 'automatic_weather',
                    ST_SetSRID(ST_Point(-45.5,-21.5),4326))
            RETURNING id
        """)
            )
        ).scalar_one()
        await session.execute(
            text("""
            INSERT INTO weather_observations (station_id, observed_at, dataset_id, ingestion_run_id)
            VALUES (:station, now(), :dataset, :run)
        """),
            {
                "station": station,
                "dataset": datasets["review/stations"],
                "run": runs["import_indicators"],
            },
        )
        await session.execute(
            text("""
            INSERT INTO followed_municipalities (user_id, municipality_code)
            VALUES ('migration-review', '9900001')
        """)
        )

        await session.run_sync(migrate, "upgrade")
        tables = await session.run_sync(lambda s: inspect(s.connection()).get_table_names())
        assert not {"indicators", "indicator_values", "saved_views"} & set(tables)
        assert {
            "territories",
            "territory_geometries",
            "weather_stations",
            "weather_observations",
            "weather_alerts",
            "followed_municipalities",
        } <= set(tables)
        remaining = (
            (
                await session.execute(
                    text("SELECT code FROM datasets WHERE name = 'Migration review'")
                )
            )
            .scalars()
            .all()
        )
        assert set(remaining) == {"malhas/review", "review/stations", "derived/review-shared"}
        assert set(
            (await session.execute(text("SELECT job FROM ingestion_runs WHERE source = 'review'")))
            .scalars()
            .all()
        ) == {"import_indicators", "import_geometries"}
        assert (
            await session.execute(
                text("SELECT COUNT(*) FROM weather_observations WHERE station_id = :id"),
                {"id": station},
            )
        ).scalar_one() == 1
        assert (
            await session.execute(
                text("SELECT ST_IsValid(geom) FROM territory_geometries WHERE territory_id = :id"),
                {"id": territory},
            )
        ).scalar_one() is True
        assert (
            await session.execute(
                text(
                    "SELECT notifications_enabled FROM followed_municipalities "
                    "WHERE user_id = 'migration-review'"
                )
            )
        ).scalar_one() is True
    finally:
        await session.rollback()
