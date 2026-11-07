"""Importa a educação básica do contexto Educação: o Censo Escolar do INEP (ADR-23).

Escolas e creches públicas por município viram indicadores do motor
(`context=education`), somados por UF, região e Brasil. A cobertura de creche
divide as matrículas pela população de 0 a 3 anos do Censo 2022 do IBGE, só nos
anos a partir dele. Tudo é baixado e lido antes de abrir a transação, como em
`import_education`; cada ano regravado apaga os valores antigos dele.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs._runner import RunReport, job_session, run_job, upsert_dataset
from app.jobs.import_education import parse_years
from app.jobs.import_indicators import persist
from app.jobs.seed_indicators import BASIC_EDUCATION_CATALOG, seed_catalog
from app.models import Indicator, IndicatorValue
from app.providers import inep_basic_education as inep
from app.providers.base import http_client
from app.providers.ibge import agregados
from app.providers.ibge.indicator_records import IndicatorObservation

JOB_NAME = "import_basic_education"
DEFAULT_YEARS = tuple(range(2019, 2026))
DEFAULT_CACHE = Path("/tmp/brasil-lens-inep")
COUNTRY_CODE = "BR"
# Antes do Censo Demográfico de 2022, a população dele não representa as crianças
# de 0 a 3 anos do ano (nascimentos caem ~3% ao ano): melhor não ter cobertura.
COVERAGE_FIRST_YEAR = 2022
COVERAGE_KEY = "basic_daycare_coverage"
_HUNDRED = Decimal(100)
_ONE_DECIMAL = Decimal("0.1")
# Tabela 9514 (Censo 2022): população residente por idade simples. Total de sexo
# e de forma de declaração; idades de menos de 1 ano a 3 anos, somadas.
POPULATION_QUERY = agregados.AggregateQuery(
    table="9514",
    variable="93",
    periods="2022",
    classification="2[6794]|287[6557,6558,6559,6560]|286[113635]",
)
_COUNTS = {
    "basic_daycare_enrollment": "daycare_enrollment",
    "basic_daycares": "daycares",
    "basic_schools": "schools",
    "basic_enrollment": "enrollment",
}


def _scopes(code: str) -> tuple[str, ...]:
    """Município, UF, região e Brasil, pelos prefixos do código IBGE."""
    return (code, code[:2], code[:1], COUNTRY_CODE)


def basic_totals(census: inep.Census, children: dict[str, int]) -> dict[str, dict[str, Decimal]]:
    """Valores por indicador e território.

    Todo município do Censo tem escola pública, então todos recebem valor; sem
    creche, o zero é medido. A cobertura de UF, região e Brasil divide a soma das
    matrículas pela soma das crianças, não é a média dos municípios.
    """
    sums: dict[str, dict[str, int]] = {key: {} for key in _COUNTS}
    for municipality, counts in census.municipalities.items():
        for key, attribute in _COUNTS.items():
            value = getattr(counts, attribute)
            for scope in _scopes(municipality):
                sums[key][scope] = sums[key].get(scope, 0) + value
    totals = {
        key: {code: Decimal(value) for code, value in values.items()}
        for key, values in sums.items()
    }
    if census.year < COVERAGE_FIRST_YEAR or not children:
        return totals
    population: dict[str, int] = {}
    for municipality, value in children.items():
        for scope in _scopes(municipality):
            population[scope] = population.get(scope, 0) + value
    enrollment = sums["basic_daycare_enrollment"]
    totals[COVERAGE_KEY] = {
        code: (Decimal(enrollment[code]) * _HUNDRED / people).quantize(_ONE_DECIMAL)
        for code, people in population.items()
        # Município criado depois do Censo 2022 não tem população; o que deixou de
        # ter escola pública (não aconteceu até 2025) também ficaria de fora.
        if people > 0 and code in enrollment
    }
    return totals


async def fetch_children() -> dict[str, int]:
    """Crianças de 0 a 3 anos por município no Censo 2022."""
    async with http_client() as client:
        observations, _ = await agregados.fetch_observations(
            client,
            POPULATION_QUERY,
            localities="N6[all]",
            accept_levels=frozenset({"N6"}),
        )
    return {item.ibge_code: int(item.value) for item in observations}


def _observations(values: dict[str, Decimal], year: int) -> list[IndicatorObservation]:
    return [
        IndicatorObservation(ibge_code=code, reference_year=year, value=value)
        for code, value in sorted(values.items())
    ]


async def _replace_year(
    session: AsyncSession,
    census: inep.Census,
    children: dict[str, int],
    *,
    dataset_id: int,
    run_id: int,
    indicators: dict[str, int],
) -> dict[str, int]:
    year = census.year
    written = 0
    totals = basic_totals(census, children)
    for seed in BASIC_EDUCATION_CATALOG:
        indicator_id = indicators[seed.key]
        await session.execute(
            delete(IndicatorValue).where(
                IndicatorValue.indicator_id == indicator_id, IndicatorValue.reference_year == year
            )
        )
        written += await persist(
            session,
            _observations(totals.get(seed.key, {}), year),
            indicator_id=indicator_id,
            dataset_id=dataset_id,
            run_id=run_id,
        )
    return {
        "municipalities": len(census.municipalities),
        "schools": sum(item.schools for item in census.municipalities.values()),
        "daycares": sum(item.daycares for item in census.municipalities.values()),
        "values": written,
    }


async def ingest(years: Iterable[int], cache_dir: Path) -> int:
    years = sorted(set(years))
    cache_dir.mkdir(parents=True, exist_ok=True)
    async with inep.client() as client:
        # Sequencial: um zip por ano (~30 MB; o de 2025, sem compressão, ~540 MB).
        censuses = [
            inep.read_census(await inep.download(client, year, cache_dir), year) for year in years
        ]
    children = await fetch_children() if years[-1] >= COVERAGE_FIRST_YEAR else {}
    async with job_session(job=JOB_NAME, source="inep") as (session, run_id, report):
        await seed_catalog(session, BASIC_EDUCATION_CATALOG)
        rows = await session.execute(
            select(Indicator.key, Indicator.id).where(
                Indicator.key.in_([seed.key for seed in BASIC_EDUCATION_CATALOG])
            )
        )
        indicators = {key: identifier for key, identifier in rows.all()}
        dataset_id = await upsert_dataset(
            session,
            source="inep",
            code="censo-escolar/microdados",
            name="INEP — Censo Escolar da Educação Básica, microdados de escolas",
            url=inep.DATASET_URL,
        )
        for census in censuses:
            details = await _replace_year(
                session,
                census,
                children,
                dataset_id=dataset_id,
                run_id=run_id,
                indicators=indicators,
            )
            report.processed += details["schools"]
            report.written += details["values"]
            report.details[str(census.year)] = details
        await session.commit()
    _print(report)
    return 0


def _print(report: RunReport) -> None:
    print(
        f"Educação básica importada: {report.processed} escolas públicas, "
        f"{report.written} valores gravados."
    )


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Importa escolas e creches públicas do Censo Escolar do INEP."
    )
    parser.add_argument(
        "--years",
        type=parse_years,
        default=list(DEFAULT_YEARS),
        help="Anos do Censo (ex.: 2025, 2022,2025 ou 2019-2025). Padrão: 2019-2025.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE,
        help="Onde guardar os zips; um zip já baixado é reaproveitado.",
    )
    args = parser.parse_args()
    return await ingest(args.years, args.cache_dir)


if __name__ == "__main__":
    run_job(main)
