"""Frescor público e proveniência devem refletir a última ingestão concluída."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.jobs._runner import upsert_dataset
from app.models import Dataset, IngestionRun, IngestionStatus
from app.repositories.weather import latest_run_per_job
from app.services.weather import _status_for

pytestmark = pytest.mark.db


async def test_dataset_upsert_refreshes_source_date_without_erasing_it_when_omitted(session):
    first_date = datetime(2026, 1, 1, tzinfo=UTC)
    second_date = first_date + timedelta(days=1)
    args = dict(source="test", code="test/review-provenance", name="Proveniência")
    identifier = await upsert_dataset(session, **args, source_updated_at=first_date)
    assert await upsert_dataset(session, **args, source_updated_at=second_date) == identifier
    await upsert_dataset(session, **args)
    updated = await session.scalar(
        select(Dataset.source_updated_at).where(Dataset.id == identifier)
    )
    assert updated == second_date


async def test_running_refresh_preserves_last_completed_source_status(session):
    job = "test_review_source_status"
    now = datetime.now(UTC)
    completed = IngestionRun(
        job=job,
        source="test",
        status=IngestionStatus.SUCCEEDED,
        started_at=now - timedelta(minutes=2),
        finished_at=now - timedelta(minutes=1),
    )
    running = IngestionRun(job=job, source="test", status=IngestionStatus.RUNNING, started_at=now)
    session.add_all([completed, running])
    await session.flush()

    rows = await latest_run_per_job(session, [job])
    status, updated_at = _status_for(rows[job], frequency_seconds=600, zero_output_key=None)
    assert status == "ok"
    assert updated_at == completed.finished_at

    running.status = IngestionStatus.FAILED
    running.finished_at = now + timedelta(seconds=1)
    await session.flush()
    rows = await latest_run_per_job(session, [job])
    status, _ = _status_for(rows[job], frequency_seconds=600, zero_output_key=None)
    assert status == "unavailable"
