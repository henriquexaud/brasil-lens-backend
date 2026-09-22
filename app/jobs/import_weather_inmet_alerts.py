"""Importa os avisos meteorológicos ativos do INMET.

`python -m app.jobs.import_weather_inmet_alerts`

Uma única requisição (`GET /avisos/ativos`) já devolve todos os avisos ativos
— sem N+1 por aviso, ao contrário das estações (ver
`import_weather_inmet_stations.py`). O polígono chega como GeoJSON e é
normalizado para `MULTIPOLYGON` válido com `ST_Multi(ST_CollectionExtract(
ST_MakeValid(...), 3))` — a mesma normalização que `import_geometries.py` já
aplica à malha do IBGE, pelo mesmo motivo: garantir um único tipo de coluna
mesmo quando a fonte manda variações (aqui, `Polygon` único por aviso).

O UPSERT em si mora em `_weather_alerts.py`, compartilhado com
`import_weather_cemaden_alerts.py` — as duas fontes gravam as mesmas colunas.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.jobs._runner import job_session, run_job, upsert_dataset
from app.jobs._weather_alerts import upsert_alerts
from app.providers.base import http_client
from app.providers.inmet import alerts

logger = get_logger(__name__)

JOB_NAME = "import_weather_inmet_alerts"


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
        report.written = await upsert_alerts(
            session,
            records,
            provider=alerts.PROVIDER_KEY,
            dataset_id=dataset_id,
            ingestion_run_id=run_id,
        )
        report.details = {"alerts": len(records)}

    logger.info("weather.inmet_alerts_imported", extra=report.details)
    print(f"Avisos INMET: {report.details['alerts']} avisos ativos gravados.")
    return 0


if __name__ == "__main__":
    run_job(main)
