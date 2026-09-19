"""Importa estações automáticas do INMET e sua leitura mais recente.

`python -m app.jobs.import_weather_inmet_stations`

Fluxo:

1. `GET /estacoes/T` — metadado de todas as estações operantes (uma
   requisição).
2. Upsert em lote de `weather_stations` (UNNEST, mesmo padrão de
   `import_indicators.py` para `indicator_values`).
3. Para cada estação, `GET /estacao/{inicio}/{fim}/{codigo}` — uma
   requisição por estação, em lotes de concorrência limitada
   (`settings.inmet_max_concurrency`), pelo mesmo motivo que a importação
   municipal do IBGE é UF-por-UF: centenas de estações de uma vez é a forma
   mais confiável de tomar timeout.
4. Upsert em lote da leitura mais recente de cada estação em
   `weather_observations`.

Chamado tanto manualmente quanto pelo laço periódico
(`app/jobs/weather_scheduler.py`) — é o mesmo `main()` nos dois casos.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.db.results import affected_rows
from app.jobs._runner import job_session, run_job, upsert_dataset
from app.providers.base import http_client
from app.providers.inmet import stations
from app.providers.records import WeatherObservationRecord, WeatherStationRecord

logger = get_logger(__name__)

JOB_NAME = "import_weather_inmet_stations"

_UPSERT_STATIONS_SQL = text(
    """
    INSERT INTO weather_stations AS target (
        provider, external_code, name, station_type, geom, state_abbreviation,
        created_at, updated_at
    )
    SELECT 'inmet', source.external_code, source.name, 'automatic_weather',
           ST_SetSRID(ST_MakePoint(source.longitude, source.latitude), 4326),
           source.state_abbreviation, now(), now()
      FROM (
          SELECT UNNEST(CAST(:codes AS text[]))      AS external_code,
                 UNNEST(CAST(:names AS text[]))       AS name,
                 UNNEST(CAST(:latitudes AS double precision[]))  AS latitude,
                 UNNEST(CAST(:longitudes AS double precision[])) AS longitude,
                 UNNEST(CAST(:states AS text[]))      AS state_abbreviation
      ) AS source
    ON CONFLICT (provider, external_code) DO UPDATE
       SET name                = EXCLUDED.name,
           geom                = EXCLUDED.geom,
           state_abbreviation  = EXCLUDED.state_abbreviation,
           updated_at          = now()
     WHERE target.name != EXCLUDED.name
        OR NOT ST_Equals(target.geom, EXCLUDED.geom)
        OR target.state_abbreviation IS DISTINCT FROM EXCLUDED.state_abbreviation
    """
)

_SELECT_STATION_IDS_SQL = text(
    """
    SELECT id, external_code
      FROM weather_stations
     WHERE provider = 'inmet' AND external_code = ANY(CAST(:codes AS text[]))
    """
)

_UPSERT_OBSERVATIONS_SQL = text(
    """
    INSERT INTO weather_observations AS target (
        station_id, observed_at, temperature_c, humidity_pct, pressure_hpa,
        precipitation_mm, dataset_id, ingestion_run_id, created_at, updated_at
    )
    SELECT UNNEST(CAST(:station_ids AS integer[])),
           UNNEST(CAST(:observed_ats AS timestamptz[])),
           UNNEST(CAST(:temperatures AS numeric[])),
           UNNEST(CAST(:humidities AS numeric[])),
           UNNEST(CAST(:pressures AS numeric[])),
           UNNEST(CAST(:precipitations AS numeric[])),
           :dataset_id, :ingestion_run_id, now(), now()
    ON CONFLICT (station_id, observed_at) DO UPDATE
       SET temperature_c    = EXCLUDED.temperature_c,
           humidity_pct     = EXCLUDED.humidity_pct,
           pressure_hpa     = EXCLUDED.pressure_hpa,
           precipitation_mm = EXCLUDED.precipitation_mm,
           dataset_id       = EXCLUDED.dataset_id,
           ingestion_run_id = EXCLUDED.ingestion_run_id,
           updated_at       = now()
     WHERE target.temperature_c    IS DISTINCT FROM EXCLUDED.temperature_c
        OR target.humidity_pct     IS DISTINCT FROM EXCLUDED.humidity_pct
        OR target.pressure_hpa     IS DISTINCT FROM EXCLUDED.pressure_hpa
        OR target.precipitation_mm IS DISTINCT FROM EXCLUDED.precipitation_mm
    """
)


async def _upsert_stations(session: AsyncSession, records: list[WeatherStationRecord]) -> int:
    if not records:
        return 0
    result = await session.execute(
        _UPSERT_STATIONS_SQL,
        {
            "codes": [r.external_code for r in records],
            "names": [r.name for r in records],
            "latitudes": [r.latitude for r in records],
            "longitudes": [r.longitude for r in records],
            "states": [r.state_abbreviation for r in records],
        },
    )
    await session.commit()
    return affected_rows(result)


async def _station_ids(session: AsyncSession, codes: list[str]) -> dict[str, int]:
    if not codes:
        return {}
    rows = await session.execute(_SELECT_STATION_IDS_SQL, {"codes": codes})
    return {external_code: station_id for station_id, external_code in rows.all()}


async def _persist_observations(
    session: AsyncSession,
    observations: list[tuple[int, WeatherObservationRecord]],
    *,
    dataset_id: int,
    ingestion_run_id: int,
) -> int:
    if not observations:
        return 0
    result = await session.execute(
        _UPSERT_OBSERVATIONS_SQL,
        {
            "station_ids": [station_id for station_id, _ in observations],
            "observed_ats": [obs.observed_at for _, obs in observations],
            "temperatures": [obs.temperature_c for _, obs in observations],
            "humidities": [obs.humidity_pct for _, obs in observations],
            "pressures": [obs.pressure_hpa for _, obs in observations],
            "precipitations": [obs.precipitation_mm for _, obs in observations],
            "dataset_id": dataset_id,
            "ingestion_run_id": ingestion_run_id,
        },
    )
    await session.commit()
    return affected_rows(result)


async def main() -> int:
    async with job_session(job=JOB_NAME, source="inmet") as (session, run_id, report):
        dataset_id = await upsert_dataset(
            session,
            source="inmet",
            code="estacoes",
            name="INMET — Estações Meteorológicas Automáticas",
            url="https://portal.inmet.gov.br/servicos/esta%C3%A7%C3%B5es-autom%C3%A1ticas",
        )
        await session.commit()

        async with http_client(
            base_url=settings.inmet_base_url, timeout=settings.inmet_http_timeout
        ) as client:
            try:
                station_records = await stations.fetch_stations(client)
            except ProviderError as exc:
                report.record_failure("estacoes", str(exc))
                report.failed += 1
                return 1

            report.processed += len(station_records)
            report.written += await _upsert_stations(session, station_records)

            codes = [record.external_code for record in station_records]
            ids_by_code = await _station_ids(session, codes)

            observations: list[tuple[int, WeatherObservationRecord]] = []
            batch_size = max(1, settings.inmet_max_concurrency)
            for start in range(0, len(codes), batch_size):
                batch = codes[start : start + batch_size]
                results = await asyncio.gather(
                    *(stations.fetch_latest_observation(client, code) for code in batch),
                    return_exceptions=True,
                )
                for code, result in zip(batch, results, strict=True):
                    station_id = ids_by_code.get(code)
                    if station_id is None:
                        continue
                    if isinstance(result, BaseException):
                        report.record_failure(f"estacao:{code}", str(result))
                        report.failed += 1
                        continue
                    if result is not None:
                        observations.append((station_id, result))

            report.written += await _persist_observations(
                session, observations, dataset_id=dataset_id, ingestion_run_id=run_id
            )
            report.details = {
                "stations": len(station_records),
                "observations": len(observations),
            }

    # Estruturado, ao contrário do `print`: este job roda sem ninguém olhando
    # o terminal a maior parte do tempo (chamado pelo scheduler a cada
    # `weather_refresh_interval_seconds`), então o resultado por ciclo
    # precisa ficar nos logs, não só na saída de uma invocação manual.
    logger.info("weather.inmet_stations_imported", extra=report.details)
    print(
        f"Estações INMET: {report.details['stations']} estações, "
        f"{report.details['observations']} leituras gravadas."
    )
    if report.failures:
        print(f"  ATENÇÃO: {len(report.failures)} estação(ões) falharam ao consultar a leitura.")
    return 0


if __name__ == "__main__":
    run_job(main)
