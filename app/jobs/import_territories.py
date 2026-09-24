"""Importa a hierarquia territorial do IBGE Localidades.

`python -m app.jobs.import_territories`

Fluxo: consultar → validar (Pydantic no provider) → converter para
`TerritoryRecord` → normalizar → upsert → registrar.

**Idempotência** vem do UNIQUE(ibge_code): o upsert atualiza nome, sigla e pai
do território existente. Rodar duas vezes não cria linha nova — e é isso que
permite reexecutar a ingestão quando o IBGE cria ou renomeia um município.

A ordem é obrigatória (país → região → UF → município) porque `parent_id` é uma
FK real: o pai precisa existir antes do filho.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.text import normalize_text
from app.db.results import affected_rows
from app.jobs._runner import RunReport, job_session, run_job, upsert_dataset
from app.models import Territory, TerritoryLevel
from app.providers.base import http_client
from app.providers.ibge import localidades
from app.providers.ibge.reference import COUNTRY_CAPITAL, STATE_CAPITALS
from app.providers.records import TerritoryRecord

logger = get_logger(__name__)

JOB_NAME = "import_territories"

# 7 binds por linha; o asyncpg aceita no máximo 32767 parâmetros por consulta.
UPSERT_BATCH_SIZE = 4000


async def _upsert_level(
    session: AsyncSession,
    records: list[TerritoryRecord],
    *,
    report: RunReport,
) -> int:
    """Upsert de um nível inteiro, resolvendo `parent_id` pelo código IBGE."""
    if not records:
        return 0

    parent_codes = {record.parent_ibge_code for record in records if record.parent_ibge_code}
    parent_ids: dict[str, int] = {}
    if parent_codes:
        rows = await session.execute(
            select(Territory.ibge_code, Territory.id).where(Territory.ibge_code.in_(parent_codes))
        )
        parent_ids = {code: identifier for code, identifier in rows.all()}

    payload = []
    for record in records:
        report.processed += 1
        parent_id = None
        if record.parent_ibge_code:
            parent_id = parent_ids.get(record.parent_ibge_code)
            if parent_id is None:
                # Não criamos território-fantasma: um pai ausente é um defeito
                # de ordem de execução e precisa ser visível.
                report.record_failure(
                    f"territory:{record.ibge_code}",
                    f"pai '{record.parent_ibge_code}' não encontrado",
                )
                report.failed += 1
                continue
        payload.append(
            {
                "ibge_code": record.ibge_code,
                "name": record.name,
                "level": record.level,
                "abbreviation": record.abbreviation,
                "parent_id": parent_id,
                "normalized_name": normalize_text(record.name),
                "normalized_abbreviation": (
                    normalize_text(record.abbreviation) if record.abbreviation else None
                ),
            }
        )

    if not payload:
        return 0

    for start in range(0, len(payload), UPSERT_BATCH_SIZE):
        statement = insert(Territory).values(payload[start : start + UPSERT_BATCH_SIZE])
        statement = statement.on_conflict_do_update(
            index_elements=[Territory.ibge_code],
            set_={
                "name": statement.excluded.name,
                "level": statement.excluded.level,
                "abbreviation": statement.excluded.abbreviation,
                "parent_id": statement.excluded.parent_id,
                "normalized_name": statement.excluded.normalized_name,
                "normalized_abbreviation": statement.excluded.normalized_abbreviation,
                "updated_at": datetime.now(UTC),
            },
        )
        await session.execute(statement)
    await session.commit()
    return len(payload)


async def _link_capitals(session: AsyncSession, report: RunReport) -> int:
    """Liga cada UF (e o país) ao município que é sua capital.

    A API Localidades não marca capitais, então o mapeamento é dado de
    referência estático (`providers/ibge/reference.py`). Aqui ele é **validado**:
    um código de capital que não exista entre os municípios importados derruba o
    job em vez de gravar FK inconsistente.
    """
    wanted = {*STATE_CAPITALS.values(), COUNTRY_CAPITAL}
    rows = await session.execute(
        select(Territory.ibge_code, Territory.id).where(Territory.ibge_code.in_(wanted))
    )
    municipality_ids = {code: identifier for code, identifier in rows.all()}

    missing = wanted - municipality_ids.keys()
    if missing:
        raise RuntimeError(
            "Capitais de referência não encontradas entre os municípios importados: "
            f"{sorted(missing)}. Verifique providers/ibge/reference.py."
        )

    pairs = [
        (localidades.COUNTRY_CODE, COUNTRY_CAPITAL),
        *STATE_CAPITALS.items(),
    ]
    linked = 0
    for territory_code, capital_code in pairs:
        result = await session.execute(
            update(Territory)
            .where(Territory.ibge_code == territory_code)
            .values(
                capital_territory_id=municipality_ids[capital_code],
                updated_at=datetime.now(UTC),
            )
        )
        linked += affected_rows(result)
    await session.commit()
    report.details["capitals_linked"] = linked
    return linked


async def main() -> int:
    async with job_session(
        job=JOB_NAME,
        source=localidades.SOURCE,
        dataset_code=localidades.DATASET_CODE,
    ) as (session, _run_id, report):
        await upsert_dataset(
            session,
            source=localidades.SOURCE,
            code=localidades.DATASET_CODE,
            name=localidades.DATASET_NAME,
            url=localidades.DATASET_URL,
        )
        await session.commit()

        async with http_client() as client:
            # Ordem obrigatória: parent_id é FK real.
            country = [localidades.country_record()]
            regions = await localidades.fetch_regions(client)
            states = await localidades.fetch_states(client)
            municipalities = await localidades.fetch_municipalities(client)

        counts: dict[str, int] = {}
        for label, records in (
            (TerritoryLevel.COUNTRY.value, country),
            (TerritoryLevel.REGION.value, regions),
            (TerritoryLevel.STATE.value, states),
            (TerritoryLevel.MUNICIPALITY.value, municipalities),
        ):
            written = await _upsert_level(session, records, report=report)
            counts[label] = written
            report.written += written
            logger.info("territories.level_imported", extra={"level": label, "written": written})

        report.details["counts"] = counts
        await _link_capitals(session, report)

    print("Territórios importados:")
    for level, written in report.details["counts"].items():
        print(f"  {level:<14} {written:>6}")
    print(f"  capitais vinculadas: {report.details.get('capitals_linked', 0)}")
    if report.failures:
        print(f"  ATENÇÃO: {len(report.failures)} escopo(s) falharam.")
    return 0


if __name__ == "__main__":
    run_job(main)
