"""Importa pluviômetros do CEMADEN.

`python -m app.jobs.import_weather_cemaden`

**Ainda sem fonte real** — ver `app/providers/cemaden/rain_gauges.py` para o
que já foi investigado e o porquê. Este job existe e roda (registrando um
`ingestion_run` com status `failed`) para que `GET /weather/sources` tenha o
que mostrar: "cemaden_rain_gauges" indisponível, com o motivo, em vez de
simplesmente não aparecer em lugar nenhum.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.jobs._runner import job_session, run_job, upsert_dataset
from app.providers.base import http_client
from app.providers.cemaden import rain_gauges

logger = get_logger(__name__)

JOB_NAME = "import_weather_cemaden"


async def main() -> int:
    async with job_session(job=JOB_NAME, source="cemaden") as (session, run_id, report):
        await upsert_dataset(
            session,
            source="cemaden",
            code="pluviometros",
            name="CEMADEN — Pluviômetros Automáticos",
            url="https://mapainterativo.cemaden.gov.br/",
        )
        await session.commit()

        async with http_client(
            base_url=settings.cemaden_base_url or "https://mapainterativo.cemaden.gov.br",
            timeout=settings.cemaden_http_timeout,
        ) as client:
            try:
                await rain_gauges.fetch_stations(client)
            except ProviderError as exc:
                logger.warning("weather.cemaden_unavailable", extra={"error": str(exc)})
                report.record_failure("pluviometros", str(exc))
                report.failed += 1
                return 1

    print("CEMADEN: fonte ainda indisponível — ver ingestion_runs.details.")
    return 1


if __name__ == "__main__":
    run_job(main)
