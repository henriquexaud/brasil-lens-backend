from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(slots=True)
class StationRow:
    provider: str
    external_code: str
    name: str
    station_type: str
    state_abbreviation: str | None
    observed_at: datetime
    temperature_c: Decimal | None
    humidity_pct: Decimal | None
    pressure_hpa: Decimal | None
    precipitation_mm: Decimal | None
    geometry_json: str


@dataclass(slots=True)
class AlertRow:
    provider: str
    external_id: str
    event: str
    severity: str
    color: str | None
    description: str | None
    onset: datetime
    expires: datetime
    affected_ibge_codes: list[str]
    risks: list[str]
    instructions: list[str]
    geometry_json: str


@dataclass(slots=True)
class SourceStatusRow:
    job: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    details: dict[str, Any] | None


_LIST_STATIONS_SQL = text(
    """
    SELECT s.provider::text        AS provider,
           s.external_code,
           s.name,
           s.station_type::text    AS station_type,
           s.state_abbreviation,
           o.observed_at,
           o.temperature_c,
           o.humidity_pct,
           o.pressure_hpa,
           o.precipitation_mm,
           ST_AsGeoJSON(s.geom)    AS geometry_json
      FROM weather_stations s
      JOIN LATERAL (
          SELECT observed_at, temperature_c, humidity_pct, pressure_hpa, precipitation_mm
            FROM weather_observations wo
           WHERE wo.station_id = s.id
           ORDER BY wo.observed_at DESC
           LIMIT 1
      ) o ON true
     ORDER BY s.external_code
    """
)

_LIST_ACTIVE_ALERTS_SQL = text(
    """
    SELECT a.provider::text        AS provider,
           a.external_id,
           a.event,
           a.severity,
           a.color,
           a.description,
           a.onset,
           a.expires,
           a.affected_ibge_codes,
           a.risks,
           a.instructions,
           ST_AsGeoJSON(a.polygon, 5) AS geometry_json
      FROM weather_alerts a
     WHERE a.expires > now()
     ORDER BY a.onset DESC
    """
)

_LATEST_RUN_PER_JOB_SQL = text(
    """
    SELECT DISTINCT ON (job) job, status::text AS status, started_at, finished_at, details
      FROM ingestion_runs
     WHERE job = ANY(CAST(:jobs AS text[]))
       AND finished_at IS NOT NULL
       AND status <> 'running'
     ORDER BY job, started_at DESC
    """
)


async def list_current_stations(session: AsyncSession) -> list[StationRow]:
    result = await session.execute(_LIST_STATIONS_SQL)
    return [
        StationRow(
            provider=row.provider,
            external_code=row.external_code,
            name=row.name,
            station_type=row.station_type,
            state_abbreviation=row.state_abbreviation,
            observed_at=row.observed_at,
            temperature_c=row.temperature_c,
            humidity_pct=row.humidity_pct,
            pressure_hpa=row.pressure_hpa,
            precipitation_mm=row.precipitation_mm,
            geometry_json=row.geometry_json,
        )
        for row in result
    ]


async def list_active_alerts(session: AsyncSession) -> list[AlertRow]:
    result = await session.execute(_LIST_ACTIVE_ALERTS_SQL)
    return [
        AlertRow(
            provider=row.provider,
            external_id=row.external_id,
            event=row.event,
            severity=row.severity,
            color=row.color,
            description=row.description,
            onset=row.onset,
            expires=row.expires,
            affected_ibge_codes=row.affected_ibge_codes,
            risks=row.risks,
            instructions=row.instructions,
            geometry_json=row.geometry_json,
        )
        for row in result
    ]


async def latest_run_per_job(session: AsyncSession, jobs: list[str]) -> dict[str, SourceStatusRow]:
    result = await session.execute(_LATEST_RUN_PER_JOB_SQL, {"jobs": jobs})
    return {
        row.job: SourceStatusRow(
            job=row.job,
            status=row.status,
            started_at=row.started_at,
            finished_at=row.finished_at,
            details=row.details,
        )
        for row in result
    }
