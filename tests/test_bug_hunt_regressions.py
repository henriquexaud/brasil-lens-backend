"""Regressões da varredura de bugs de 2026-09-23."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs import import_territories
from app.jobs._runner import RunReport
from app.jobs._weather_alerts import upsert_alerts
from app.models import IngestionStatus, TerritoryLevel
from app.providers.records import TerritoryRecord, WeatherAlertRecord
from app.repositories import territories as territories_repo
from app.services import fire_summary as summary_service

ASYNCPG_MAX_PARAMS = 32767


class _RecordingSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []
        self.commits = 0

    async def execute(self, statement: Any) -> None:
        self.statements.append(statement)

    async def commit(self) -> None:
        self.commits += 1


async def test_territory_upsert_stays_under_the_driver_parameter_limit() -> None:
    records = [
        TerritoryRecord(
            ibge_code=f"{index:07d}",
            name=f"Município {index}",
            level=TerritoryLevel.COUNTRY,
            parent_ibge_code=None,
        )
        for index in range(5571)
    ]
    session = _RecordingSession()
    report = RunReport()

    written = await import_territories._upsert_level(session, records, report=report)  # type: ignore[arg-type]

    assert written == 5571
    assert len(session.statements) > 1
    assert session.commits == 1
    inserted = 0
    for statement in session.statements:
        compiled = statement.compile()
        assert len(compiled.params) <= ASYNCPG_MAX_PARAMS
        inserted += len(statement._multi_values[0])
    assert inserted == 5571


def test_status_is_partial_when_a_rerun_writes_nothing_but_some_scopes_succeeded() -> None:
    report = RunReport(processed=27, written=0, failed=1)
    report.record_failure("state:12", "timeout")
    assert report.status is IngestionStatus.PARTIAL


def test_status_is_failed_when_every_scope_failed() -> None:
    report = RunReport(processed=2, written=0, failed=2)
    report.record_failure("state:11", "timeout")
    report.record_failure("state:12", "timeout")
    assert report.status is IngestionStatus.FAILED


def test_search_wildcards_are_escaped() -> None:
    compiled = territories_repo._search_filter("50%_").compile()
    assert "ESCAPE" in str(compiled)
    assert "50/%/_" in compiled.params.values()


@pytest.mark.db
async def test_percent_search_matches_nothing(session: AsyncSession) -> None:
    rows = await territories_repo.list_territories(
        session, level=TerritoryLevel.MUNICIPALITY, search="%%"
    )
    assert rows == []


async def test_fire_summary_window_ignores_sub_second_part_of_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = datetime.now(UTC).replace(microsecond=345678) - timedelta(seconds=5)
    hours = 24
    boundary = at.replace(microsecond=0) - timedelta(hours=hours)
    seen: dict[str, Any] = {}

    async def scope(*_args: Any, **_kwargs: Any) -> str:
        return "id_0=33"

    async def wfs(cql: str, _count: int) -> tuple[list[Any], int]:
        seen["cql"] = cql
        return [], 1

    async def rows(_cql: str, _total: int) -> list[dict[str, str]]:
        return [
            {
                "id_foco_bdq": "1",
                "id_2": "1100001",
                "data_hora_gmt": boundary.isoformat(),
            }
        ]

    async def areas(_session: Any) -> list[dict[str, Any]]:
        return [{"ibge_code": "1100001", "name": "Teste", "state": "RO", "area_km2": 10.0}]

    monkeypatch.setattr(summary_service, "_scope_filter", scope)
    monkeypatch.setattr(summary_service, "_fetch_wfs", wfs)
    monkeypatch.setattr(summary_service, "_fetch_rows", rows)
    monkeypatch.setattr(summary_service, "municipality_areas", areas)
    monkeypatch.setattr(summary_service, "state_areas", areas)
    monkeypatch.setattr(summary_service, "_cache", _NoCache())
    monkeypatch.setattr(summary_service, "_failures", _NoCache())

    result = await summary_service.get_summary(
        None,  # type: ignore[arg-type]
        level="brazil",
        parent=None,
        hours=hours,
        at=at,
    )

    assert result.total == 1
    assert result.window_end.microsecond == 0


class _NoCache:
    def get(self, _key: str) -> None:
        return None

    def set(self, *_args: Any, **_kwargs: Any) -> None:
        return None


@pytest_asyncio.fixture
async def alert_dataset(session: AsyncSession) -> AsyncIterator[int]:
    try:
        yield (
            await session.execute(
                text(
                    "INSERT INTO datasets (source, code, name) "
                    "VALUES ('test', 'test/alerts', 'Dataset de teste') RETURNING id"
                )
            )
        ).scalar_one()
    finally:
        await session.rollback()


@pytest.mark.db
async def test_alert_upsert_refreshes_text_only_revisions(
    session: AsyncSession,
    alert_dataset: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session, "commit", session.flush)
    now = datetime.now(UTC)
    polygon = {
        "type": "Polygon",
        "coordinates": [[[-50, -10], [-49, -10], [-49, -9], [-50, -9], [-50, -10]]],
    }

    def record(**overrides: Any) -> WeatherAlertRecord:
        values: dict[str, Any] = {
            "provider": "inmet",
            "external_id": "TEST-ALERT-1",
            "event": "Chuvas Intensas",
            "severity": "Perigo",
            "onset": now,
            "expires": now + timedelta(hours=6),
            "polygon_geojson": polygon,
            "color": "#ff0",
            "risks": ("Alagamentos",),
            "instructions": ("Evite áreas de risco",),
            "affected_ibge_codes": ("1100001",),
        }
        values.update(overrides)
        return WeatherAlertRecord(**values)

    async def upsert(rec: WeatherAlertRecord) -> int:
        return await upsert_alerts(
            session, [rec], provider="inmet", dataset_id=alert_dataset, ingestion_run_id=None
        )  # type: ignore[arg-type]

    assert await upsert(record()) == 1
    assert await upsert(record()) == 0
    assert await upsert(record(event="Tempestade")) == 1
    assert await upsert(record(event="Tempestade", risks=("Queda de árvores",))) == 1
    assert (
        await upsert(
            record(
                event="Tempestade",
                risks=("Queda de árvores",),
                affected_ibge_codes=("1100001", "1100002"),
            )
        )
        == 1
    )
    stored = (
        await session.execute(
            text(
                "SELECT event, risks, affected_ibge_codes FROM weather_alerts "
                "WHERE external_id = 'TEST-ALERT-1'"
            )
        )
    ).one()
    assert stored.event == "Tempestade"
    assert stored.risks == ["Queda de árvores"]
    assert stored.affected_ibge_codes == ["1100001", "1100002"]
