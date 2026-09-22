"""Importa os alertas de risco geo-hidrológico ativos do CEMADEN.

`python -m app.jobs.import_weather_cemaden_alerts`

Mesmo formato de `import_weather_inmet_alerts.py`: uma única requisição WFS
(`GetFeature`) já devolve todos os alertas em vigor, sem N+1. O UPSERT é o
mesmo dos dois jobs, em `_weather_alerts.py` — só o `provider` muda.

Ver `app/providers/cemaden/alerts.py` para o contrato da fonte e as decisões
de normalização (evento sem o sufixo de nível, expiração derivada de
`vigencia`, `status=1` como filtro de "ainda ativo").
"""

from __future__ import annotations

from app.core.config import settings
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.jobs._runner import job_session, run_job, upsert_dataset
from app.jobs._weather_alerts import upsert_alerts
from app.providers.base import http_client
from app.providers.cemaden import alerts

logger = get_logger(__name__)

JOB_NAME = "import_weather_cemaden_alerts"


async def main() -> int:
    async with job_session(job=JOB_NAME, source="cemaden") as (session, run_id, report):
        dataset_id = await upsert_dataset(
            session,
            source="cemaden",
            code="alertas",
            name="CEMADEN — Alertas de Risco Geo-Hidrológico",
            url="https://mapainterativo.cemaden.gov.br/",
        )
        await session.commit()

        async with http_client(
            base_url=settings.cemaden_alerts_base_url, timeout=settings.cemaden_http_timeout
        ) as client:
            try:
                records = await alerts.fetch_active_alerts(client)
            except ProviderError as exc:
                report.record_failure("alertas", str(exc))
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

    logger.info("weather.cemaden_alerts_imported", extra=report.details)
    print(f"Alertas CEMADEN: {report.details['alerts']} alertas ativos gravados.")
    return 0


if __name__ == "__main__":
    run_job(main)
