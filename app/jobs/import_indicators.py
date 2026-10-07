from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.results import affected_rows
from app.jobs._runner import job_session, run_job, upsert_dataset
from app.jobs.seed_indicators import seed_catalog
from app.models import Indicator, Territory, TerritoryLevel
from app.providers.base import http_client
from app.providers.ibge import agregados
from app.providers.ibge.datasets import DERIVED_INDICATORS, SOURCED_INDICATORS, SourcedIndicatorSpec
from app.providers.ibge.indicator_records import IndicatorObservation
from app.services.derived import derive

UPSERT_INDICATOR_VALUES_SQL = text("""
    INSERT INTO socioeconomic_values AS target
        (territory_id, indicator_id, reference_year, value, dataset_id, ingestion_run_id)
    SELECT t.id, :indicator_id, source.year, source.value, :dataset_id, :run_id
      FROM unnest(CAST(:codes AS text[]), CAST(:years AS smallint[]), CAST(:values AS numeric[]))
           AS source(code, year, value)
      JOIN territories t ON t.ibge_code = source.code
    ON CONFLICT (territory_id, indicator_id, reference_year) DO UPDATE
       SET value = EXCLUDED.value, dataset_id = EXCLUDED.dataset_id,
           ingestion_run_id = EXCLUDED.ingestion_run_id, updated_at = now()
     WHERE target.value IS DISTINCT FROM EXCLUDED.value
        OR target.dataset_id IS DISTINCT FROM EXCLUDED.dataset_id
""")


@dataclass(frozen=True)
class Options:
    periods: str | None = None
    latest: bool = False
    states: tuple[str, ...] | None = None
    indicators: tuple[str, ...] | None = None
    skip_municipalities: bool = False


async def persist(
    session: AsyncSession,
    observations: list[IndicatorObservation],
    *,
    indicator_id: int,
    dataset_id: int,
    run_id: int,
) -> int:
    if not observations:
        return 0
    result = await session.execute(
        UPSERT_INDICATOR_VALUES_SQL,
        {
            "codes": [item.ibge_code for item in observations],
            "years": [item.reference_year for item in observations],
            "values": [item.value for item in observations],
            "indicator_id": indicator_id,
            "dataset_id": dataset_id,
            "run_id": run_id,
        },
    )
    return affected_rows(result)


async def import_spec(
    session: AsyncSession,
    client: httpx.AsyncClient,
    spec: SourcedIndicatorSpec,
    *,
    options: Options,
    indicator_id: int,
    run_id: int,
) -> dict[str, int]:
    available = await agregados.fetch_available_periods(client, spec.table)
    requested = options.periods or spec.periods
    periods = (
        available
        if requested == "all"
        else [
            period
            for period in available
            if period in requested.split("|") or period[:4] in requested.split("|")
        ]
    )
    if options.latest and periods:
        latest_year = max(period[:4] for period in periods)
        periods = [period for period in periods if period.startswith(latest_year)]
    if not periods:
        return {"observations": 0, "written": 0, "discarded": 0}
    query = agregados.AggregateQuery(
        table=spec.table,
        variable=spec.variable,
        periods="|".join(periods),
        classification=spec.classification,
    )
    dataset_id = await upsert_dataset(
        session, source="ibge", code=spec.dataset_code, name=spec.dataset_name, url=spec.dataset_url
    )
    state_query = select(Territory.ibge_code).where(Territory.level == TerritoryLevel.STATE)
    if options.states:
        state_query = state_query.where(Territory.ibge_code.in_(options.states))
    states = list((await session.execute(state_query.order_by(Territory.ibge_code))).scalars())
    scopes: list[tuple[str, frozenset[str]]] = []
    if spec.national_levels:
        scopes.append(
            (
                "|".join(f"{level}[all]" for level in spec.national_levels),
                frozenset(spec.national_levels),
            )
        )
    if spec.includes_municipalities and not options.skip_municipalities:
        scopes.extend((f"N6[N3[{code}]]", frozenset({"N6"})) for code in states)
    counts = {"observations": 0, "written": 0, "discarded": 0}
    batch_size = min(4, max(1, settings.ibge_max_concurrency))
    for start in range(0, len(scopes), batch_size):
        batch = scopes[start : start + batch_size]
        results = await asyncio.gather(
            *(
                agregados.fetch_observations(
                    client,
                    query,
                    localities=localities,
                    multiplier=spec.value_multiplier,
                    accept_levels=levels,
                )
                for localities, levels in batch
            ),
            return_exceptions=True,
        )
        # Falha de fonte interrompe a ingestão; a transação preserva o snapshot anterior.
        for result in results:
            if isinstance(result, BaseException):
                raise result
            observations, discarded = result
            counts["observations"] += len(observations)
            counts["discarded"] += discarded
            counts["written"] += await persist(
                session,
                observations,
                indicator_id=indicator_id,
                dataset_id=dataset_id,
                run_id=run_id,
            )
    return counts


async def ingest(options: Options) -> int:
    keys = set(options.indicators or ())
    known = {spec.indicator_key for spec in SOURCED_INDICATORS} | {
        spec.indicator_key for spec in DERIVED_INDICATORS
    }
    if keys - known:
        raise ValueError(f"Indicadores desconhecidos: {', '.join(sorted(keys - known))}.")
    for derived_spec in reversed(DERIVED_INDICATORS):
        if derived_spec.indicator_key in keys:
            keys.update(derived_spec.dependencies)
    async with job_session(job="import_indicators", source="ibge") as (session, run_id, report):
        await seed_catalog(session)
        ids = {
            key: identifier
            for key, identifier in (
                await session.execute(select(Indicator.key, Indicator.id))
            ).all()
        }
        async with http_client() as client:
            for spec in SOURCED_INDICATORS:
                if keys and spec.indicator_key not in keys:
                    continue
                counts = await import_spec(
                    session,
                    client,
                    spec,
                    options=options,
                    indicator_id=ids[spec.indicator_key],
                    run_id=run_id,
                )
                report.processed += counts["observations"]
                report.written += counts["written"]
                report.details[spec.dataset_code] = counts
        for derived_spec in DERIVED_INDICATORS:
            dataset_id = await upsert_dataset(
                session,
                source="brasil-lens",
                code=derived_spec.dataset_code,
                name=derived_spec.dataset_name,
            )
            outcome = await derive(
                session,
                derived_spec,
                indicator_ids=ids,
                dataset_id=dataset_id,
                ingestion_run_id=run_id,
            )
            report.written += outcome.rows_written
    print(
        f"Indicadores importados: {report.processed} observações, "
        f"{report.written} valores gravados."
    )
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Importa a camada socioeconômica do IBGE, sem consultar fontes climáticas."
    )
    parser.add_argument(
        "--periods", help="Períodos desejados, ex.: 2021|2022|2023. Padrão: série completa."
    )
    parser.add_argument(
        "--latest", action="store_true", help="Importa apenas o último ano publicado de cada fonte."
    )
    parser.add_argument("--states", help="Restringe municípios às UFs informadas, ex.: 35,31.")
    parser.add_argument(
        "--indicators", help="Restringe indicadores e suas dependências, ex.: population,gdp."
    )
    parser.add_argument("--skip-municipalities", action="store_true")
    args = parser.parse_args()
    if args.states and any(len(code) != 2 or not code.isdigit() for code in args.states.split(",")):
        parser.error("Informe códigos de UF com dois dígitos, separados por vírgula.")
    return await ingest(
        Options(
            periods=args.periods,
            latest=args.latest,
            states=tuple(args.states.split(",")) if args.states else None,
            indicators=tuple(args.indicators.split(",")) if args.indicators else None,
            skip_municipalities=args.skip_municipalities,
        )
    )


if __name__ == "__main__":
    run_job(main)
