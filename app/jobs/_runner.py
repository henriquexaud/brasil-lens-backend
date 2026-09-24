"""Infraestrutura comum dos jobs de ingestão.

Responsabilidades: registrar a execução em `ingestion_runs`, garantir upsert de
`datasets` e padronizar a saída no terminal. Nada de fila, agendador ou
orquestrador — CLI é suficiente para o MVP.

**Falha parcial é um resultado de primeira classe.** Cada escopo (uma UF, um
dataset) commita separadamente; se 3 de 27 UFs falharem, as outras 24 ficam
gravadas e o run termina com `status='partial'`, listando os escopos falhos em
`details`. O contrário — abortar tudo — descartaria dezenas de MB de download
por causa de um 503 momentâneo.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import configure_logging, get_logger
from app.db.session import SessionFactory, dispose_engine
from app.models import Dataset, IngestionRun, IngestionStatus

logger = get_logger(__name__)


@dataclass
class RunReport:
    """Acumulador do resultado de um job."""

    processed: int = 0
    written: int = 0
    failed: int = 0
    details: dict[str, Any] = field(default_factory=dict)
    failures: list[dict[str, str]] = field(default_factory=list)

    def record_failure(self, scope: str, error: str) -> None:
        self.failures.append({"scope": scope, "error": error})
        logger.warning("ingestion.scope_failed", extra={"scope": scope, "error": error})

    @property
    def status(self) -> IngestionStatus:
        # `written` conta só linhas alteradas: num re-run idempotente ele é 0
        # mesmo com escopos bem-sucedidos, então `processed` também conta.
        if self.failures and self.written == 0 and self.processed <= self.failed:
            return IngestionStatus.FAILED
        if self.failures:
            return IngestionStatus.PARTIAL
        return IngestionStatus.SUCCEEDED


async def start_run(
    session: AsyncSession,
    *,
    job: str,
    source: str,
    dataset_code: str | None = None,
) -> int:
    run = IngestionRun(
        job=job,
        source=source,
        dataset_code=dataset_code,
        status=IngestionStatus.RUNNING,
        records_processed=0,
        records_written=0,
        records_failed=0,
    )
    session.add(run)
    await session.flush()
    await session.commit()
    logger.info("ingestion.started", extra={"run": run.id, "job": job})
    return run.id


async def finish_run(
    session: AsyncSession,
    run_id: int,
    report: RunReport,
    *,
    error: str | None = None,
    status: IngestionStatus | None = None,
) -> None:
    run = (
        await session.execute(select(IngestionRun).where(IngestionRun.id == run_id))
    ).scalar_one()
    run.status = status or (IngestionStatus.FAILED if error else report.status)
    run.finished_at = datetime.now(UTC)
    run.records_processed = report.processed
    run.records_written = report.written
    run.records_failed = report.failed
    run.error = error
    details = dict(report.details)
    if report.failures:
        details["failures"] = report.failures
    run.details = details or None
    await session.commit()
    logger.info(
        "ingestion.finished",
        extra={
            "run": run_id,
            "status": run.status.value,
            "processed": report.processed,
            "written": report.written,
            "failed": report.failed,
        },
    )


async def upsert_dataset(
    session: AsyncSession,
    *,
    source: str,
    code: str,
    name: str,
    url: str | None = None,
    source_updated_at: datetime | None = None,
) -> int:
    """Garante o dataset e devolve seu id.

    Idempotente via UNIQUE (source, code): reexecutar atualiza nome/URL em vez
    de criar uma linha nova.
    """
    statement = (
        insert(Dataset)
        .values(
            source=source,
            code=code,
            name=name,
            url=url,
            source_updated_at=source_updated_at,
        )
        .on_conflict_do_update(
            constraint="uq_datasets_source_code",
            set_={"name": name, "url": url, "updated_at": datetime.now(UTC)},
        )
        .returning(Dataset.id)
    )
    dataset_id = (await session.execute(statement)).scalar_one()
    return dataset_id


@asynccontextmanager
async def job_session(
    *, job: str, source: str, dataset_code: str | None = None
) -> AsyncIterator[tuple[AsyncSession, int, RunReport]]:
    """Abre sessão, registra o run e garante fechamento do registro.

    A sessão é entregue sem transação aberta: cada job commita por escopo.
    """
    async with SessionFactory() as session:
        run_id = await start_run(session, job=job, source=source, dataset_code=dataset_code)
        report = RunReport()
        try:
            yield session, run_id, report
        except Exception as exc:
            await session.rollback()
            await finish_run(session, run_id, report, error=f"{type(exc).__name__}: {exc}")
            raise
        else:
            await finish_run(session, run_id, report)


def run_job(main: Callable[[], Awaitable[int]]) -> None:
    """Ponto de entrada padrão dos módulos de job (`python -m app.jobs.x`)."""
    configure_logging()

    async def _wrapped() -> int:
        try:
            return await main()
        finally:
            await dispose_engine()

    raise SystemExit(asyncio.run(_wrapped()))
