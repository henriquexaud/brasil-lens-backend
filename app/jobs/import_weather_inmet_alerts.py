"""Importa os avisos meteorológicos ativos do INMET.

`python -m app.jobs.import_weather_inmet_alerts`

Uma única requisição (`GET /avisos/ativos`) já devolve todos os avisos ativos
— sem N+1 por aviso, ao contrário das estações (ver
`import_weather_inmet_stations.py`). O polígono chega como GeoJSON e é
normalizado para `MULTIPOLYGON` válido com `ST_Multi(ST_CollectionExtract(
ST_MakeValid(...), 3))` — a mesma normalização que `import_geometries.py` já
aplica à malha do IBGE, pelo mesmo motivo: garantir um único tipo de coluna
mesmo quando a fonte manda variações (aqui, `Polygon` único por aviso).
"""

from __future__ import annotations

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.db.results import affected_rows
from app.jobs._runner import job_session, run_job, upsert_dataset
from app.providers.base import http_client
from app.providers.inmet import alerts
from app.providers.records import WeatherAlertRecord

logger = get_logger(__name__)

JOB_NAME = "import_weather_inmet_alerts"

_UPSERT_ALERTS_SQL = text(
    """
    INSERT INTO weather_alerts AS target (
        provider, external_id, event, severity, color, onset, expires, polygon,
        affected_ibge_codes, risks, instructions, dataset_id, ingestion_run_id,
        created_at, updated_at
    )
    SELECT 'inmet', source.external_id, source.event, source.severity, source.color,
           source.onset, source.expires,
           ST_Multi(ST_CollectionExtract(
               ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(source.polygon_geojson), 4326)), 3
           )),
           source.affected_ibge_codes::jsonb, source.risks::jsonb, source.instructions::jsonb,
           :dataset_id, :ingestion_run_id, now(), now()
      FROM (
          SELECT UNNEST(CAST(:external_ids AS text[]))  AS external_id,
                 UNNEST(CAST(:events AS text[]))         AS event,
                 UNNEST(CAST(:severities AS text[]))     AS severity,
                 UNNEST(CAST(:colors AS text[]))         AS color,
                 UNNEST(CAST(:onsets AS timestamptz[]))  AS onset,
                 UNNEST(CAST(:expirations AS timestamptz[])) AS expires,
                 UNNEST(CAST(:polygons AS text[]))       AS polygon_geojson,
                 UNNEST(CAST(:affected AS text[]))       AS affected_ibge_codes,
                 UNNEST(CAST(:risks AS text[]))          AS risks,
                 UNNEST(CAST(:instructions AS text[]))   AS instructions
      ) AS source
    ON CONFLICT (provider, external_id) DO UPDATE
       SET event                = EXCLUDED.event,
           severity             = EXCLUDED.severity,
           color                = EXCLUDED.color,
           onset                = EXCLUDED.onset,
           expires              = EXCLUDED.expires,
           polygon              = EXCLUDED.polygon,
           affected_ibge_codes  = EXCLUDED.affected_ibge_codes,
           risks                = EXCLUDED.risks,
           instructions         = EXCLUDED.instructions,
           dataset_id           = EXCLUDED.dataset_id,
           ingestion_run_id     = EXCLUDED.ingestion_run_id,
           updated_at           = now()
     WHERE target.expires  IS DISTINCT FROM EXCLUDED.expires
        OR target.severity IS DISTINCT FROM EXCLUDED.severity
        OR NOT ST_Equals(target.polygon, EXCLUDED.polygon)
    """
)

# Alertas que já saíram da fonte (revogados) não desaparecem sozinhos daqui —
# `GET /weather/alerts` já filtra por `expires > now()`, então uma linha
# vencida simplesmente para de aparecer sem precisar de DELETE.


async def _upsert_alerts(
    session: AsyncSession,
    records: list[WeatherAlertRecord],
    *,
    dataset_id: int,
    ingestion_run_id: int,
) -> int:
    if not records:
        return 0
    result = await session.execute(
        _UPSERT_ALERTS_SQL,
        {
            "external_ids": [r.external_id for r in records],
            "events": [r.event for r in records],
            "severities": [r.severity for r in records],
            "colors": [r.color for r in records],
            "onsets": [r.onset for r in records],
            "expirations": [r.expires for r in records],
            "polygons": [orjson.dumps(r.polygon_geojson).decode() for r in records],
            "affected": [orjson.dumps(list(r.affected_ibge_codes)).decode() for r in records],
            "risks": [orjson.dumps(list(r.risks)).decode() for r in records],
            "instructions": [orjson.dumps(list(r.instructions)).decode() for r in records],
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
            code="avisos",
            name="INMET — Avisos Meteorológicos",
            url="https://alertas2.inmet.gov.br/",
        )
        await session.commit()

        async with http_client(
            base_url=settings.inmet_alerts_base_url, timeout=settings.inmet_http_timeout
        ) as client:
            try:
                records = await alerts.fetch_active_alerts(client)
            except ProviderError as exc:
                report.record_failure("avisos", str(exc))
                report.failed += 1
                return 1

        report.processed = len(records)
        report.written = await _upsert_alerts(
            session, records, dataset_id=dataset_id, ingestion_run_id=run_id
        )
        report.details = {"alerts": len(records)}

    logger.info("weather.inmet_alerts_imported", extra=report.details)
    print(f"Avisos INMET: {report.details['alerts']} avisos ativos gravados.")
    return 0


if __name__ == "__main__":
    run_job(main)
