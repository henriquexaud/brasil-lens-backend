"""Importa os valores dos indicadores e recalcula os derivados.

`python -m app.jobs.import_indicators [--periods 2020|2021|2022] [--skip-municipalities]`

Fluxo, para cada entrada de `providers/ibge/datasets.py`:

1. **consultar** a API de Agregados v3 do IBGE;
2. **validar** a resposta com os modelos Pydantic do provider;
3. **converter** para `IndicatorObservation`;
4. **normalizar** — descartar sentinelas (`"..."`, `"-"`), somar variáveis
   complementares (serviços privados + administração pública), reduzir períodos
   sub-anuais à média do ano (a taxa de desocupação é trimestral) e aplicar o
   multiplicador da fonte (a tabela 5938 publica PIB em milhares de reais);
5. **persistir** com `ON CONFLICT ... DO UPDATE` sobre a PK
   `(territory_id, indicator_id, reference_year)`;
6. **registrar** contagens e escopos falhos em `ingestion_runs`.

Ao final, os indicadores derivados são recalculados a partir do que acabou de
entrar — é por isso que eles ficam sempre coerentes com suas bases. São três
formas de derivação (razão, crescimento e participação no total nacional), todas
em `services/derived.py`; o job só resolve dependências e proveniência.

**Idempotência:** a PK composta é a chave natural do dado. Executar duas vezes
com a mesma fonte atualiza `updated_at` e não cria nenhuma linha nova.

Níveis territoriais: Brasil/região/UF vêm em **uma** requisição
(`N1[all]|N2[all]|N3[all]`); o nível municipal é consultado **UF por UF**
(`N6[N3[35]]`), porque pedir 5.571 municípios × 20 anos de uma vez é a forma
mais confiável de tomar timeout.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.db.results import affected_rows
from app.jobs._runner import RunReport, job_session, run_job, upsert_dataset
from app.models import Indicator, Territory, TerritoryLevel
from app.providers.base import http_client
from app.providers.ibge import agregados
from app.providers.ibge.datasets import (
    DERIVED_INDICATORS,
    MUNICIPAL_LEVEL,
    SOURCED_INDICATORS,
    SourcedIndicatorSpec,
)
from app.providers.ibge.records import IndicatorObservation
from app.services.derived import derive

logger = get_logger(__name__)

JOB_NAME = "import_indicators"

# Upsert em lote. O `WHERE` no DO UPDATE evita reescrever linhas idênticas: é o
# que mantém `updated_at` significando "quando o valor mudou de fato" e o que
# torna uma reexecução literalmente sem efeito.
#
# Nome público porque é testado diretamente em tests/test_ingestion_idempotency.py:
# duplicar o SQL no teste testaria uma cópia, não a ingestão real.
UPSERT_INDICATOR_VALUES_SQL = text(
    """
    INSERT INTO indicator_values AS target (
        territory_id, indicator_id, reference_year, value,
        dataset_id, ingestion_run_id, created_at, updated_at
    )
    SELECT t.id, :indicator_id, source.reference_year, source.value,
           :dataset_id, :ingestion_run_id, now(), now()
      FROM (
          SELECT UNNEST(CAST(:codes AS text[]))      AS ibge_code,
                 UNNEST(CAST(:years AS smallint[]))  AS reference_year,
                 UNNEST(CAST(:values AS numeric[])) AS value
      ) AS source
      JOIN territories t ON t.ibge_code = source.ibge_code
    ON CONFLICT (territory_id, indicator_id, reference_year) DO UPDATE
       SET value            = EXCLUDED.value,
           dataset_id       = EXCLUDED.dataset_id,
           ingestion_run_id = EXCLUDED.ingestion_run_id,
           updated_at       = now()
     WHERE target.value      IS DISTINCT FROM EXCLUDED.value
        OR target.dataset_id IS DISTINCT FROM EXCLUDED.dataset_id
    """
)


@dataclass(frozen=True, slots=True)
class _Options:
    periods: str | None
    skip_municipalities: bool
    states: tuple[str, ...] | None


async def _indicator_ids(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(select(Indicator.key, Indicator.id))
    return {key: identifier for key, identifier in rows.all()}


async def _state_codes(session: AsyncSession, requested: tuple[str, ...] | None) -> list[str]:
    statement = select(Territory.ibge_code).where(Territory.level == TerritoryLevel.STATE)
    if requested:
        statement = statement.where(Territory.ibge_code.in_(requested))
    return list((await session.execute(statement.order_by(Territory.ibge_code))).scalars())


async def _persist(
    session: AsyncSession,
    observations: list[IndicatorObservation],
    *,
    indicator_id: int,
    dataset_id: int,
    ingestion_run_id: int,
) -> int:
    """Grava um lote de observações. Devolve quantas linhas mudaram.

    Territórios desconhecidos são silenciosamente ignorados pelo JOIN — nunca
    criamos território a partir de um valor. A diferença entre observações
    enviadas e linhas afetadas é reportada pelo chamador.
    """
    if not observations:
        return 0
    result = await session.execute(
        UPSERT_INDICATOR_VALUES_SQL,
        {
            "indicator_id": indicator_id,
            "dataset_id": dataset_id,
            "ingestion_run_id": ingestion_run_id,
            "codes": [observation.ibge_code for observation in observations],
            "years": [observation.reference_year for observation in observations],
            "values": [observation.value for observation in observations],
        },
    )
    await session.commit()
    return affected_rows(result)


async def _import_spec(
    session: AsyncSession,
    client: httpx.AsyncClient,
    spec: SourcedIndicatorSpec,
    *,
    indicator_id: int,
    ingestion_run_id: int,
    options: _Options,
    report: RunReport,
) -> dict[str, int]:
    dataset_id = await upsert_dataset(
        session,
        source=agregados.SOURCE,
        code=spec.dataset_code,
        name=spec.dataset_name,
        url=spec.dataset_url,
    )
    await session.commit()

    query = agregados.AggregateQuery(
        table=spec.table,
        variable=spec.variable,
        periods=options.periods or spec.periods,
        classification=spec.classification,
    )
    counts = {"observations": 0, "written": 0, "discarded": 0}

    # 1) Brasil + regiões + UFs em uma única requisição.
    national_levels = spec.national_levels
    if national_levels:
        localities = "|".join(f"{level}[all]" for level in national_levels)
        try:
            observations, discarded = await agregados.fetch_observations(
                client,
                query,
                localities=localities,
                multiplier=spec.value_multiplier,
                accept_levels=frozenset(national_levels),
            )
        except ProviderError as exc:
            report.record_failure(f"{spec.dataset_code}:national", str(exc))
            report.failed += 1
        else:
            counts["observations"] += len(observations)
            counts["discarded"] += discarded
            counts["written"] += await _persist(
                session,
                observations,
                indicator_id=indicator_id,
                dataset_id=dataset_id,
                ingestion_run_id=ingestion_run_id,
            )

    # 2) Municípios, UF por UF, com concorrência limitada.
    if spec.includes_municipalities and not options.skip_municipalities:
        state_codes = await _state_codes(session, options.states)
        batch_size = max(1, settings.ibge_max_concurrency)
        for start in range(0, len(state_codes), batch_size):
            batch = state_codes[start : start + batch_size]
            results = await asyncio.gather(
                *(
                    agregados.fetch_observations(
                        client,
                        query,
                        localities=f"{MUNICIPAL_LEVEL}[N3[{code}]]",
                        multiplier=spec.value_multiplier,
                        accept_levels=frozenset({MUNICIPAL_LEVEL}),
                    )
                    for code in batch
                ),
                return_exceptions=True,
            )
            for code, result in zip(batch, results, strict=True):
                if isinstance(result, BaseException):
                    report.record_failure(f"{spec.dataset_code}:state:{code}", str(result))
                    report.failed += 1
                    continue
                observations, discarded = result
                counts["observations"] += len(observations)
                counts["discarded"] += discarded
                counts["written"] += await _persist(
                    session,
                    observations,
                    indicator_id=indicator_id,
                    dataset_id=dataset_id,
                    ingestion_run_id=ingestion_run_id,
                )

    logger.info(
        "indicators.spec_imported",
        extra={"indicator": spec.indicator_key, "dataset": spec.dataset_code, **counts},
    )
    return counts


async def _recompute_derived(
    session: AsyncSession,
    *,
    indicator_ids: dict[str, int],
    ingestion_run_id: int,
    report: RunReport,
) -> dict[str, int]:
    results: dict[str, int] = {}
    for spec in DERIVED_INDICATORS:
        missing = [
            key for key in (spec.indicator_key, *spec.dependencies) if key not in indicator_ids
        ]
        if missing:
            report.record_failure(
                f"derived:{spec.indicator_key}",
                f"indicadores ausentes no catálogo: {missing}. Rode seed_indicators.",
            )
            report.failed += 1
            continue

        dataset_id = await upsert_dataset(
            session,
            source="brasil-lens",
            code=spec.dataset_code,
            name=spec.dataset_name,
        )
        await session.commit()

        outcome = await derive(
            session,
            spec,
            indicator_ids=indicator_ids,
            dataset_id=dataset_id,
            ingestion_run_id=ingestion_run_id,
        )
        await session.commit()
        results[spec.indicator_key] = outcome.rows_written
    return results


async def main() -> int:
    options = _parse_args()

    async with job_session(job=JOB_NAME, source=agregados.SOURCE) as (session, run_id, report):
        indicator_ids = await _indicator_ids(session)
        if not indicator_ids:
            raise RuntimeError(
                "Catálogo de indicadores vazio. Rode 'python -m app.jobs.seed_indicators'."
            )

        per_dataset: dict[str, dict[str, int]] = {}
        async with http_client() as client:
            for spec in SOURCED_INDICATORS:
                indicator_id = indicator_ids.get(spec.indicator_key)
                if indicator_id is None:
                    report.record_failure(
                        f"{spec.dataset_code}",
                        f"indicador '{spec.indicator_key}' não está no catálogo",
                    )
                    report.failed += 1
                    continue
                counts = await _import_spec(
                    session,
                    client,
                    spec,
                    indicator_id=indicator_id,
                    ingestion_run_id=run_id,
                    options=options,
                    report=report,
                )
                per_dataset[spec.dataset_code] = counts
                report.processed += counts["observations"]
                report.written += counts["written"]

        derived = await _recompute_derived(
            session,
            indicator_ids=indicator_ids,
            ingestion_run_id=run_id,
            report=report,
        )
        report.written += sum(derived.values())
        report.details = {"datasets": per_dataset, "derived": derived}

    print("Indicadores importados:")
    for dataset_code, counts in report.details["datasets"].items():
        print(
            f"  {dataset_code:<28} {counts['observations']:>8} obs  "
            f"{counts['written']:>8} gravadas  {counts['discarded']:>6} descartadas"
        )
    print("Derivados recalculados:")
    for key, written in report.details["derived"].items():
        print(f"  {key:<28} {written:>8} linhas")
    if report.failures:
        print(f"  ATENÇÃO: {len(report.failures)} escopo(s) falharam (ver ingestion_runs.details).")
    return 0


def _parse_args() -> _Options:
    parser = argparse.ArgumentParser(description="Importa indicadores do IBGE.")
    parser.add_argument(
        "--periods",
        help=(
            "Sobrepõe os períodos de todas as fontes. Ex.: '2022' ou '2021|2022|2023'. "
            "Padrão: 'all' (série completa)."
        ),
    )
    parser.add_argument(
        "--skip-municipalities",
        action="store_true",
        help="Importa apenas Brasil, regiões e UFs (ingestão rápida).",
    )
    parser.add_argument("--states", help="Limita o nível municipal às UFs informadas (ex.: 35,31).")
    args = parser.parse_args()
    return _Options(
        periods=args.periods,
        skip_municipalities=args.skip_municipalities,
        states=tuple(code.strip() for code in args.states.split(",")) if args.states else None,
    )


if __name__ == "__main__":
    run_job(main)
