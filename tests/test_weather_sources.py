from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
import pytest

from app.core.config import settings
from app.repositories import weather as weather_repo
from app.repositories.weather import AlertRow, SourceStatusRow
from app.schemas.weather import (
    WeatherAlertCategory,
    WeatherAlertSeverityLevel,
    WeatherSourceStatusValue,
)
from app.services import weather as service

OK = WeatherSourceStatusValue.OK
STALE = WeatherSourceStatusValue.STALE
UNAVAILABLE = WeatherSourceStatusValue.UNAVAILABLE
FREQUENCY = 600


def _run(
    *,
    status: str = "succeeded",
    finished_ago: timedelta | None = timedelta(minutes=1),
    details: dict[str, Any] | None = None,
) -> SourceStatusRow:
    now = datetime.now(UTC)
    return SourceStatusRow(
        job="import_weather_inmet_alerts",
        status=status,
        started_at=now - timedelta(minutes=2),
        finished_at=None if finished_ago is None else now - finished_ago,
        details=details,
    )


@pytest.fixture(autouse=True)
def _fresh_alerts_cache() -> None:
    service._alerts_cache.clear()


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        ("Grande Perigo", WeatherAlertSeverityLevel.EXTREME),
        ("Extremo", WeatherAlertSeverityLevel.EXTREME),
        ("Muito Alto", WeatherAlertSeverityLevel.VERY_HIGH),
        ("Perigo Potencial", WeatherAlertSeverityLevel.MODERATE),
        ("Moderado", WeatherAlertSeverityLevel.MODERATE),
        ("Perigo", WeatherAlertSeverityLevel.HIGH),
        ("Alto", WeatherAlertSeverityLevel.HIGH),
        ("  GRANDE PERIGO  ", WeatherAlertSeverityLevel.EXTREME),
        ("Desconhecida", WeatherAlertSeverityLevel.MODERATE),
        ("", WeatherAlertSeverityLevel.MODERATE),
    ],
)
def test_inmet_severity_text_maps_to_a_level(
    severity: str, expected: WeatherAlertSeverityLevel
) -> None:
    assert service._severity_level_for("inmet", severity) is expected


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        ("Muito Alto", WeatherAlertSeverityLevel.VERY_HIGH),
        ("Alto", WeatherAlertSeverityLevel.HIGH),
        ("Moderado", WeatherAlertSeverityLevel.MODERATE),
        ("Observação", WeatherAlertSeverityLevel.MODERATE),
    ],
)
def test_cemaden_severity_text_maps_to_a_level(
    severity: str, expected: WeatherAlertSeverityLevel
) -> None:
    assert service._severity_level_for("cemaden", severity) is expected


def test_the_same_word_means_different_levels_in_each_provider() -> None:
    """ "Perigo" é alto no INMET; no CEMADEN, só "alto"/"muito alto" sobem do moderado."""
    assert service._severity_level_for("inmet", "Perigo") is WeatherAlertSeverityLevel.HIGH
    assert service._severity_level_for("cemaden", "Perigo") is WeatherAlertSeverityLevel.MODERATE


def test_only_cemaden_is_geo_hydrological() -> None:
    assert service._category_for("cemaden") is WeatherAlertCategory.GEO_HYDROLOGICAL
    assert service._category_for("inmet") is WeatherAlertCategory.METEOROLOGICAL
    assert service._category_for("outro") is WeatherAlertCategory.METEOROLOGICAL


def test_a_source_that_never_ran_is_unavailable() -> None:
    assert service._status_for(None, frequency_seconds=FREQUENCY, zero_output_key=None) == (
        UNAVAILABLE,
        None,
    )


@pytest.mark.parametrize(
    "row",
    [_run(status="failed"), _run(finished_ago=None)],
    ids=["failed run", "run still in progress"],
)
def test_a_failed_or_unfinished_run_is_unavailable_with_no_timestamp(row: SourceStatusRow) -> None:
    assert service._status_for(row, frequency_seconds=FREQUENCY, zero_output_key=None) == (
        UNAVAILABLE,
        None,
    )


def test_a_recent_run_is_ok_and_reports_when_it_finished() -> None:
    row = _run(finished_ago=timedelta(seconds=30))

    status, updated_at = service._status_for(row, frequency_seconds=FREQUENCY, zero_output_key=None)

    assert status is OK
    assert updated_at == row.finished_at


def test_a_partial_run_still_counts_as_fresh() -> None:
    row = _run(status="partial")
    assert service._status_for(row, frequency_seconds=FREQUENCY, zero_output_key=None)[0] is OK


def test_a_source_turns_stale_only_after_three_missed_intervals() -> None:
    inside = _run(finished_ago=timedelta(seconds=FREQUENCY * 3 - 30))
    outside = _run(finished_ago=timedelta(seconds=FREQUENCY * 3 + 30))

    assert service._status_for(inside, frequency_seconds=FREQUENCY, zero_output_key=None)[0] is OK
    status, updated_at = service._status_for(
        outside, frequency_seconds=FREQUENCY, zero_output_key=None
    )
    assert status is STALE
    assert updated_at == outside.finished_at, "stale ainda diz quando foi a última atualização"


@pytest.mark.parametrize("details", [None, {}, {"stations": 0}])
def test_a_run_that_produced_nothing_is_unavailable_when_output_is_required(
    details: dict[str, Any] | None,
) -> None:
    row = _run(details=details)
    assert service._status_for(row, frequency_seconds=FREQUENCY, zero_output_key="stations") == (
        UNAVAILABLE,
        None,
    )


def test_a_run_with_output_is_ok_when_output_is_required() -> None:
    row = _run(details={"stations": 12})
    status, _ = service._status_for(row, frequency_seconds=FREQUENCY, zero_output_key="stations")
    assert status is OK


async def test_sources_report_each_provider_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = {
        "import_weather_inmet_alerts": _run(finished_ago=timedelta(minutes=1)),
        "import_weather_cemaden_alerts": _run(status="failed"),
    }

    async def latest(_: Any, jobs: list[str]) -> dict[str, SourceStatusRow]:
        assert set(jobs) == set(runs)
        return runs

    monkeypatch.setattr(weather_repo, "latest_run_per_job", latest)

    response = await service.get_sources(None)  # type: ignore[arg-type]

    by_key = {source.key: source for source in response.sources}
    assert by_key["inmet_alerts"].status is OK
    assert by_key["cemaden_alerts"].status is UNAVAILABLE
    assert by_key["cemaden_alerts"].last_updated_at is None
    assert {source.update_frequency_seconds for source in response.sources} == {
        settings.weather_refresh_interval_seconds
    }


def _alert_row(external_id: str, severity: str = "Perigo") -> AlertRow:
    now = datetime.now(UTC)
    return AlertRow(
        provider="inmet",
        external_id=external_id,
        event="Chuvas Intensas",
        severity=severity,
        color=None,
        description=None,
        onset=now,
        expires=now + timedelta(hours=6),
        affected_ibge_codes=[],
        risks=[],
        instructions=[],
        geometry_json='{"type":"Polygon","coordinates":[[[-44,-20],[-43,-20],[-43,-19],[-44,-20]]]}',
    )


async def test_alerts_are_read_once_per_cache_window_and_etag_follows_the_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[list[AlertRow]] = [[_alert_row("A-1")], [_alert_row("A-1"), _alert_row("A-2")]]

    async def rows(_: Any) -> list[AlertRow]:
        return reads.pop(0)

    monkeypatch.setattr(weather_repo, "list_active_alerts", rows)

    first = await service.get_alerts(None)  # type: ignore[arg-type]
    cached = await service.get_alerts(None)  # type: ignore[arg-type]
    assert cached is first
    assert len(reads) == 1, "a segunda leitura veio do cache, não do banco"

    service._alerts_cache.clear()
    changed = await service.get_alerts(None)  # type: ignore[arg-type]
    assert changed.etag != first.etag
    assert first.etag.startswith('W/"alerts:')


async def test_alert_features_carry_category_level_and_the_original_severity_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def rows(_: Any) -> list[AlertRow]:
        return [_alert_row("A-9", severity="Grande Perigo")]

    monkeypatch.setattr(weather_repo, "list_active_alerts", rows)

    body = (await service.get_alerts(None)).body  # type: ignore[arg-type]

    feature = orjson.loads(body)["features"][0]
    assert feature["id"] == "inmet:A-9"
    assert feature["properties"]["severity"] == "Grande Perigo"
    assert feature["properties"]["severityLevel"] == "extreme"
    assert feature["properties"]["category"] == "meteorological"
