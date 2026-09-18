"""Registro de execuções de ingestão.

Escopo deliberadamente pequeno: responde "de onde veio, quando entrou, deu certo?"
sem virar plataforma de jobs nem data lineage.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Enum, Identity, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IngestionStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    # `PARTIAL` é essencial: cada escopo (UF, dataset) commita separadamente, e
    # perder 24 UFs porque 3 falharam seria pior que registrar sucesso parcial.
    PARTIAL = "partial"
    FAILED = "failed"


ingestion_status_enum = Enum(
    IngestionStatus,
    name="ingestion_status",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_code: Mapped[str | None] = mapped_column(String(96))

    status: Mapped[IngestionStatus] = mapped_column(
        ingestion_status_enum,
        nullable=False,
        default=IngestionStatus.RUNNING,
    )
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    records_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error: Mapped[str | None] = mapped_column(Text)
    # Contagens por escopo e lista de escopos falhos. JSONB para o schema não
    # precisar mudar a cada job novo.
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (Index("ix_ingestion_runs_job_started_at", "job", "started_at"),)

    def __repr__(self) -> str:  # pragma: no cover - diagnóstico
        return f"<IngestionRun {self.id} {self.job} {self.status.value}>"
