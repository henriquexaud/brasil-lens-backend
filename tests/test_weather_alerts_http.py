from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.jobs._weather_alerts import upsert_alerts
from app.main import app
from app.providers.records import WeatherAlertRecord
from app.repositories import weather as weather_repo
from app.repositories.weather import AlertRow
from app.services import weather as weather_service


@pytest.fixture(autouse=True)
def _fresh_alerts_cache() -> None:
    weather_service._alerts_cache.clear()


async def test_polling_an_unchanged_alert_list_gets_an_empty_304(monkeypatch) -> None:
    now = datetime.now(UTC)
    row = AlertRow(
        provider="inmet",
        external_id="A-1",
        event="Chuvas Intensas",
        severity="Perigo",
        color="#ff0",
        description=None,
        onset=now,
        expires=now + timedelta(hours=6),
        affected_ibge_codes=["3106200"],
        risks=[],
        instructions=[],
        geometry_json='{"type":"Polygon","coordinates":[[[-44,-20],[-43,-20],[-43,-19],[-44,-20]]]}',
    )

    async def rows(_: Any) -> list[AlertRow]:
        return [row]

    async def no_session() -> AsyncIterator[None]:
        yield None

    monkeypatch.setattr(weather_repo, "list_active_alerts", rows)
    app.dependency_overrides[get_session] = no_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.get("/api/v1/weather/alerts")
            again = await client.get(
                "/api/v1/weather/alerts", headers={"If-None-Match": first.headers["etag"]}
            )
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-cache"
    assert first.json()["features"][0]["properties"]["severityLevel"]
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["etag"] == first.headers["etag"]


@pytest.mark.db
async def test_alert_polygons_are_served_with_five_decimal_places(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session, "commit", session.flush)
    dataset = (
        await session.execute(
            text(
                "INSERT INTO datasets (source, code, name) "
                "VALUES ('test', 'test/alerts-precision', 'Dataset de teste') RETURNING id"
            )
        )
    ).scalar_one()
    now = datetime.now(UTC)
    ring = [
        [-50.123456789, -10.987654321],
        [-49.1, -10.2],
        [-49.3, -9.4],
        [-50.123456789, -10.987654321],
    ]
    await upsert_alerts(
        session,
        [
            WeatherAlertRecord(
                provider="inmet",
                external_id="TEST-PRECISION",
                event="Chuvas Intensas",
                severity="Perigo",
                onset=now,
                expires=now + timedelta(hours=6),
                polygon_geojson={"type": "Polygon", "coordinates": [ring]},
            )
        ],
        provider="inmet",
        dataset_id=dataset,
        ingestion_run_id=None,
    )  # type: ignore[arg-type]

    [alert] = [
        a
        for a in await weather_repo.list_active_alerts(session)
        if a.external_id == "TEST-PRECISION"
    ]
    multipolygon = orjson.loads(alert.geometry_json)["coordinates"]
    points = [tuple(point) for polygon in multipolygon for ring in polygon for point in ring]
    assert (-50.12346, -10.98765) in points
    assert all(round(value, 5) == value for point in points for value in point)
