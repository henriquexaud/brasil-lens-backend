from __future__ import annotations

import argparse
import asyncio
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import delete, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs._runner import job_session, run_job, upsert_dataset
from app.models import PoliticalCandidate, PoliticalRelease, PoliticalResult, Territory
from app.providers.tse import ElectionStage


def chunks(rows: Iterator[dict[str, Any]]) -> Iterator[list[dict[str, Any]]]:
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) == 1000:
            yield batch
            batch = []
    if batch:
        yield batch


async def publish(
    session: AsyncSession, stage: ElectionStage, run_id: int, metadata: dict[str, Any]
) -> int:
    # Publicação atômica; linhas idênticas não são regravadas a cada atualização.
    await upsert_dataset(
        session,
        source="TSE",
        code=f"elections_{stage.year}",
        name=f"Eleições {stage.year}",
        url=f"https://dadosabertos.tse.jus.br/dataset/resultados-{stage.year}",
        source_updated_at=stage.updated_at,
    )
    release = insert(PoliticalRelease).values(
        year=stage.year, run_id=run_id, metadata_json=metadata
    )
    await session.execute(
        release.on_conflict_do_update(
            index_elements=[PoliticalRelease.year],
            set_={"run_id": run_id, "metadata_json": metadata},
        )
    )
    old_candidates = set(
        (
            await session.execute(
                select(PoliticalCandidate.candidate_id).where(PoliticalCandidate.year == stage.year)
            )
        ).scalars()
    )
    written = 0
    for batch in chunks(stage.candidate_rows()):
        for row in batch:
            old_candidates.discard(row["candidate_id"])
        statement = insert(PoliticalCandidate).values(batch)
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[PoliticalCandidate.year, PoliticalCandidate.candidate_id],
                set_={
                    key: getattr(statement.excluded, key)
                    for key in ["office", "scope_code", "elected_round", "data"]
                },
                where=or_(
                    *(
                        getattr(PoliticalCandidate, key).is_distinct_from(
                            getattr(statement.excluded, key)
                        )
                        for key in ["office", "scope_code", "elected_round", "data"]
                    )
                ),
            )
        )
        written += len(batch)
    old_results = {
        tuple(row)
        for row in (
            await session.execute(
                select(
                    PoliticalResult.office, PoliticalResult.round, PoliticalResult.territory_code
                ).where(PoliticalResult.year == stage.year)
            )
        ).all()
    }
    for batch in chunks(stage.result_rows()):
        for row in batch:
            old_results.discard((row["office"], row["round"], row["territory_code"]))
        statement = insert(PoliticalResult).values(batch)
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    PoliticalResult.year,
                    PoliticalResult.office,
                    PoliticalResult.round,
                    PoliticalResult.territory_code,
                ],
                set_={"data": statement.excluded.data},
                where=PoliticalResult.data.is_distinct_from(statement.excluded.data),
            )
        )
        written += len(batch)
    stale_candidates = list(old_candidates)
    for offset in range(0, len(stale_candidates), 1000):
        await session.execute(
            delete(PoliticalCandidate).where(
                PoliticalCandidate.year == stage.year,
                PoliticalCandidate.candidate_id.in_(stale_candidates[offset : offset + 1000]),
            )
        )
    stale_results = list(old_results)
    for offset in range(0, len(stale_results), 1000):
        await session.execute(
            delete(PoliticalResult).where(
                PoliticalResult.year == stage.year,
                tuple_(
                    PoliticalResult.office, PoliticalResult.round, PoliticalResult.territory_code
                ).in_(stale_results[offset : offset + 1000]),
            )
        )
    return written


async def main() -> int:
    parser = argparse.ArgumentParser(description="Importar resumos eleitorais oficiais do TSE.")
    parser.add_argument("--year", type=int, required=True, choices=[2022, 2024, 2026])
    parser.add_argument("--cache-dir", type=Path, default=Path("/tmp/brasil-lens-tse"))
    parser.add_argument(
        "--refresh", action="store_true", help="Baixar novamente os arquivos oficiais."
    )
    parser.add_argument(
        "--complete",
        action="store_true",
        help="Marcar o pleito como concluído após a publicação final do TSE.",
    )
    args = parser.parse_args()
    async with job_session(job="import_elections", source="TSE", dataset_code=str(args.year)) as (
        session,
        run_id,
        report,
    ):
        territories = set((await session.execute(select(Territory.ibge_code))).scalars())
        await session.rollback()
        if not territories:
            raise ValueError("Importe a malha IBGE antes dos resultados eleitorais.")
        with tempfile.TemporaryDirectory(prefix="brasil-lens-election-") as directory:
            stage = ElectionStage(Path(directory) / "stage.sqlite", args.year, territories)
            try:
                await asyncio.to_thread(stage.ingest, args.cache_dir, args.refresh)
                metadata = stage.metadata()
                if args.complete:
                    metadata["status"] = "ok"
                if not metadata["contests"]:
                    raise ValueError(
                        "O TSE ainda não publicou resultados utilizáveis para este ano."
                    )
                report.written = await publish(session, stage, run_id, metadata)
                report.processed = stage.processed
                report.details = metadata
            finally:
                stage.db.close()
    return 0


if __name__ == "__main__":
    run_job(main)
