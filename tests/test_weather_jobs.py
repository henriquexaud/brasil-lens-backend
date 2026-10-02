from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.errors import ProviderError
from app.jobs import (
    _runner,
    import_weather_cemaden_alerts,
    import_weather_inmet_alerts,
    weather_scheduler,
)
from app.jobs._runner import RunReport
from app.models import IngestionStatus
from app.providers.records import WeatherAlertRecord

ALERT_JOBS = [import_weather_inmet_alerts, import_weather_cemaden_alerts]
ALERT_JOB_IDS = ["inmet", "cemaden"]


def _record(external_id: str = "A-1") -> WeatherAlertRecord:
    now = datetime.now(UTC)
    return WeatherAlertRecord(
        provider="inmet",
        external_id=external_id,
        event="Chuvas Intensas",
        severity="Perigo",
        onset=now,
        expires=now,
        polygon_geojson={"type": "Polygon", "coordinates": []},
    )


def _patch_job(
    monkeypatch: pytest.MonkeyPatch, job: ModuleType, report: RunReport, *, fetch: AsyncMock
) -> AsyncMock:
    @asynccontextmanager
    async def job_session(**_kwargs: Any) -> AsyncIterator[tuple[AsyncMock, int, RunReport]]:
        yield AsyncMock(), 7, report

    @asynccontextmanager
    async def http_client(**_kwargs: Any) -> AsyncIterator[object]:
        yield object()

    upsert = AsyncMock(return_value=1)
    monkeypatch.setattr(job, "job_session", job_session)
    monkeypatch.setattr(job, "http_client", http_client)
    monkeypatch.setattr(job, "upsert_dataset", AsyncMock(return_value=3))
    monkeypatch.setattr(job, "upsert_alerts", upsert)
    monkeypatch.setattr(job.alerts, "fetch_active_alerts", fetch)
    return upsert


@pytest.mark.parametrize("job", ALERT_JOBS, ids=ALERT_JOB_IDS)
async def test_alert_job_stores_what_the_provider_returned(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], job: ModuleType
) -> None:
    report = RunReport()
    records = [_record("A-1"), _record("A-2")]
    upsert = _patch_job(monkeypatch, job, report, fetch=AsyncMock(return_value=records))

    assert await job.main() == 0

    upsert.assert_awaited_once()
    assert upsert.await_args.args[1] == records
    assert upsert.await_args.kwargs["dataset_id"] == 3
    assert upsert.await_args.kwargs["ingestion_run_id"] == 7
    assert (report.processed, report.written, report.failed) == (2, 1, 0)
    assert report.details == {"alerts": 2}
    assert "2 " in capsys.readouterr().out


@pytest.mark.parametrize("job", ALERT_JOBS, ids=ALERT_JOB_IDS)
async def test_alert_job_with_no_active_alerts_succeeds(
    monkeypatch: pytest.MonkeyPatch, job: ModuleType
) -> None:
    report = RunReport()
    _patch_job(monkeypatch, job, report, fetch=AsyncMock(return_value=[]))

    assert await job.main() == 0
    assert report.status is IngestionStatus.SUCCEEDED
    assert report.details == {"alerts": 0}


@pytest.mark.parametrize("job", ALERT_JOBS, ids=ALERT_JOB_IDS)
async def test_alert_job_provider_failure_is_recorded_and_stored_alerts_are_left_alone(
    monkeypatch: pytest.MonkeyPatch, job: ModuleType
) -> None:
    report = RunReport()
    upsert = _patch_job(
        monkeypatch, job, report, fetch=AsyncMock(side_effect=ProviderError("fonte fora do ar"))
    )

    assert await job.main() == 1

    upsert.assert_not_awaited()
    assert report.failed == 1
    assert report.failures[0]["error"] == "fonte fora do ar"
    assert report.status is IngestionStatus.FAILED


class _FakeSession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.fixture
def run_log(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    log: dict[str, Any] = {"session": _FakeSession(), "finished": []}

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[_FakeSession]:
        yield log["session"]

    async def finish(_session: Any, run_id: int, report: RunReport, *, error: str | None = None):
        log["finished"].append((run_id, report, error))

    monkeypatch.setattr(_runner, "SessionFactory", session_factory)
    monkeypatch.setattr(_runner, "start_run", AsyncMock(return_value=11))
    monkeypatch.setattr(_runner, "finish_run", finish)
    return log


async def test_job_session_closes_a_clean_run_with_the_report(run_log: dict[str, Any]) -> None:
    async with _runner.job_session(job="j", source="s") as (_, run_id, report):
        report.processed = report.written = 4

    [(finished_id, finished_report, error)] = run_log["finished"]
    assert (run_id, finished_id) == (11, 11)
    assert finished_report is report
    assert error is None
    assert not run_log["session"].rolled_back


async def test_job_session_rolls_back_marks_the_run_failed_and_reraises(
    run_log: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="quebrou"):
        async with _runner.job_session(job="j", source="s"):
            raise ValueError("quebrou")

    [(_, _, error)] = run_log["finished"]
    assert error == "ValueError: quebrou"
    assert run_log["session"].rolled_back


class _Calls(list[str]):
    """Registro das execuções; `until` acorda a cada chamada, sem polling com sleep."""

    def __init__(self) -> None:
        super().__init__()
        self.changed = asyncio.Event()

    async def until(self, condition: Callable[[list[str]], bool]) -> None:
        async with asyncio.timeout(2):
            while not condition(self):
                self.changed.clear()
                await self.changed.wait()


@pytest.fixture
def scheduler(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _Calls]:
    monkeypatch.setattr(settings, "weather_refresh_enabled", True)
    monkeypatch.setattr(settings, "weather_refresh_interval_seconds", 0)
    monkeypatch.setattr(weather_scheduler, "_tasks", [])

    def install(**jobs: Callable[[], Any]) -> _Calls:
        calls = _Calls()

        def tracked(name: str, job: Callable[[], Any]) -> Callable[[], Any]:
            async def run() -> int:
                calls.append(name)
                calls.changed.set()
                return await job()

            return run

        monkeypatch.setattr(
            weather_scheduler, "_JOBS", tuple((n, tracked(n, j)) for n, j in jobs.items())
        )
        return calls

    return install


async def test_scheduler_keeps_running_after_one_cycle_fails(
    scheduler: Callable[..., _Calls],
) -> None:
    attempts = 0

    async def flaky() -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("ciclo ruim")
        return 0

    calls = scheduler(inmet=flaky)

    weather_scheduler.start()
    try:
        await calls.until(lambda made: len(made) >= 3)
    finally:
        await weather_scheduler.stop()

    assert attempts >= 3, "uma falha não derruba o laço"


async def test_scheduler_runs_every_job_and_stop_cancels_them(
    scheduler: Callable[..., _Calls],
) -> None:
    async def ok() -> int:
        return 0

    calls = scheduler(inmet=ok, cemaden=ok)

    weather_scheduler.start()
    await calls.until(lambda made: {"inmet", "cemaden"} <= set(made))
    tasks = list(weather_scheduler._tasks)
    await weather_scheduler.stop()

    assert len(tasks) == 2
    assert all(task.done() for task in tasks)
    assert weather_scheduler._tasks == []


async def test_scheduler_start_is_idempotent(scheduler: Callable[..., _Calls]) -> None:
    async def ok() -> int:
        return 0

    scheduler(inmet=ok)

    weather_scheduler.start()
    weather_scheduler.start()
    try:
        assert len(weather_scheduler._tasks) == 1
    finally:
        await weather_scheduler.stop()


async def test_scheduler_does_nothing_when_refresh_is_disabled(
    monkeypatch: pytest.MonkeyPatch, scheduler: Callable[..., _Calls]
) -> None:
    async def ok() -> int:
        return 0

    calls = scheduler(inmet=ok)
    monkeypatch.setattr(settings, "weather_refresh_enabled", False)

    weather_scheduler.start()
    await asyncio.sleep(0)

    assert weather_scheduler._tasks == []
    assert calls == []
