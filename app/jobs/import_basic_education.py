"""Importa a educação básica do contexto Educação: o Censo Escolar do INEP (ADR-23).

Escolas públicas e matrículas por município e etapa viram indicadores do motor
(`context=education`), somados por UF, região e Brasil. O atendimento de cada
etapa divide as matrículas pela população da faixa etária dela no Censo 2022 do
IBGE, só nos anos a partir dele. As escolas do último ano pedido substituem a lista
de escolas por município (`basic_schools`). Tudo é baixado e lido antes de abrir a
transação, como em `import_education`; cada ano regravado apaga os valores antigos dele.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.results import affected_rows
from app.jobs._runner import RunReport, job_session, run_job, upsert_dataset
from app.jobs.import_education import parse_years
from app.jobs.import_indicators import persist
from app.jobs.seed_indicators import (
    BASIC_EDUCATION_CATALOG,
    RETIRED_BASIC_KEYS,
    basic_key,
    seed_catalog,
)
from app.models import BasicSchool, Indicator, IndicatorValue
from app.providers import inep_basic_education as inep
from app.providers.base import http_client
from app.providers.ibge import agregados
from app.providers.ibge.indicator_records import IndicatorObservation

JOB_NAME = "import_basic_education"
DEFAULT_YEARS = tuple(range(2019, 2026))
DEFAULT_CACHE = Path("/tmp/brasil-lens-inep")
COUNTRY_CODE = "BR"
# Antes do Censo Demográfico de 2022, a população dele não representa as crianças
# do ano (nascimentos caem ~3% ao ano): melhor não ter atendimento.
COVERAGE_FIRST_YEAR = 2022
_HUNDRED = Decimal(100)
_ONE_DECIMAL = Decimal("0.1")
# Tabela 9514 (Censo 2022): população residente por idade simples. As categorias
# 6557 a 6574 são as idades de menos de 1 ano a 17 anos, em sequência.
_FIRST_AGE_CATEGORY = 6557
# Só as etapas com atendimento (ver BASIC_STAGES): educação infantil e ensino médio.
AGE_BANDS = {
    "early_childhood": range(0, 6),
    "high_school": range(15, 18),
}

INSERT_SCHOOLS_SQL = text("""
    INSERT INTO basic_schools
        (code, year, territory_id, name, network, enrollment, early_childhood_enrollment,
         elementary_enrollment, high_school_enrollment, dataset_id)
    SELECT source.code, :year, t.id, source.name, source.network, source.enrollment,
           source.early_childhood, source.elementary, source.high_school, :dataset_id
      FROM unnest(CAST(:codes AS integer[]), CAST(:municipalities AS text[]),
                  CAST(:names AS text[]), CAST(:networks AS text[]),
                  CAST(:enrollment AS integer[]), CAST(:early_childhood AS integer[]),
                  CAST(:elementary AS integer[]), CAST(:high_school AS integer[]))
           AS source(code, municipality, name, network, enrollment, early_childhood,
                     elementary, high_school)
      JOIN territories t ON t.ibge_code = source.municipality AND t.level = 'municipality'
""")


def population_query(ages: range) -> agregados.AggregateQuery:
    """Soma das idades da faixa, com o total de sexo e de forma de declaração."""
    categories = ",".join(str(_FIRST_AGE_CATEGORY + age) for age in ages)
    return agregados.AggregateQuery(
        table="9514",
        variable="93",
        periods="2022",
        classification=f"2[6794]|287[{categories}]|286[113635]",
    )


def _scopes(code: str) -> tuple[str, ...]:
    """Município, UF, região e Brasil, pelos prefixos do código IBGE."""
    return (code, code[:2], code[:1], COUNTRY_CODE)


def _sum_by_scope(values: Iterable[tuple[str, int]]) -> dict[str, int]:
    sums: dict[str, int] = {}
    for municipality, value in values:
        for scope in _scopes(municipality):
            sums[scope] = sums.get(scope, 0) + value
    return sums


def basic_totals(
    census: inep.Census, population: dict[str, dict[str, int]]
) -> dict[str, dict[str, Decimal]]:
    """Valores por indicador e território.

    Todo município do Censo tem escola pública, então todos recebem valor em
    cada etapa; sem a etapa, o zero é medido. O atendimento de UF, região e
    Brasil divide a soma das matrículas pela soma da população da faixa, não é a
    média dos municípios.
    """
    municipalities = census.municipalities.items()
    totals: dict[str, dict[str, Decimal]] = {}
    for stage in inep.STAGES:
        for metric in ("schools", "enrollment"):
            sums = _sum_by_scope(
                (code, getattr(counts, metric)[stage]) for code, counts in municipalities
            )
            totals[basic_key(stage, metric)] = {code: Decimal(v) for code, v in sums.items()}
    if census.year < COVERAGE_FIRST_YEAR:
        return totals
    for stage, people_by_municipality in population.items():
        people = _sum_by_scope(people_by_municipality.items())
        enrollment = totals[basic_key(stage, "enrollment")]
        totals[basic_key(stage, "coverage")] = {
            code: (enrollment[code] * _HUNDRED / count).quantize(_ONE_DECIMAL)
            for code, count in people.items()
            # Município criado depois do Censo 2022 não tem população; o que deixou
            # de ter escola pública (não aconteceu até 2025) também ficaria de fora.
            if count > 0 and code in enrollment
        }
    return totals


async def fetch_population() -> dict[str, dict[str, int]]:
    """População de cada faixa etária das etapas, por município, no Censo 2022.

    Uma consulta por faixa: todas as idades juntas passam de 100 mil valores.
    """
    population: dict[str, dict[str, int]] = {}
    async with http_client() as client:
        for stage, ages in AGE_BANDS.items():
            observations, _ = await agregados.fetch_observations(
                client,
                population_query(ages),
                localities="N6[all]",
                accept_levels=frozenset({"N6"}),
            )
            population[stage] = {item.ibge_code: int(item.value) for item in observations}
    return population


async def replace_schools(session: AsyncSession, census: inep.Census, dataset_id: int) -> int:
    """Troca a lista inteira pelas escolas do Censo; devolve quantas têm município na malha."""
    await session.execute(delete(BasicSchool))
    schools = census.schools
    return affected_rows(
        await session.execute(
            INSERT_SCHOOLS_SQL,
            {
                "year": census.year,
                "dataset_id": dataset_id,
                "codes": [item.code for item in schools],
                "municipalities": [item.municipality for item in schools],
                "names": [item.name for item in schools],
                "networks": [item.network for item in schools],
                **{
                    stage: [item.enrollment[stage] for item in schools]
                    for stage in ("early_childhood", "elementary", "high_school")
                },
                "enrollment": [item.enrollment["all"] for item in schools],
            },
        )
    )


def _observations(values: dict[str, Decimal], year: int) -> list[IndicatorObservation]:
    return [
        IndicatorObservation(ibge_code=code, reference_year=year, value=value)
        for code, value in sorted(values.items())
    ]


async def _replace_year(
    session: AsyncSession,
    census: inep.Census,
    population: dict[str, dict[str, int]],
    *,
    dataset_id: int,
    run_id: int,
    indicators: dict[str, int],
) -> dict[str, int]:
    year = census.year
    written = 0
    totals = basic_totals(census, population)
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
        "schools": sum(item.schools["all"] for item in census.municipalities.values()),
        "early_childhood": sum(
            item.schools["early_childhood"] for item in census.municipalities.values()
        ),
        "values": written,
    }


async def ingest(years: Iterable[int], cache_dir: Path) -> int:
    years = sorted(set(years))
    cache_dir.mkdir(parents=True, exist_ok=True)
    async with inep.client() as client:
        # Sequencial: um zip por ano (~30 MB; o de 2025, sem compressão, ~540 MB).
        censuses = [
            inep.read_census(
                await inep.download(client, year, cache_dir), year, schools=year == years[-1]
            )
            for year in years
        ]
    population = await fetch_population() if years[-1] >= COVERAGE_FIRST_YEAR else {}
    async with job_session(job=JOB_NAME, source="inep") as (session, run_id, report):
        await session.execute(delete(Indicator).where(Indicator.key.in_(RETIRED_BASIC_KEYS)))
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
                population,
                dataset_id=dataset_id,
                run_id=run_id,
                indicators=indicators,
            )
            report.processed += details["schools"]
            report.written += details["values"]
            report.details[str(census.year)] = details
        listed = await replace_schools(session, censuses[-1], dataset_id)
        # Município fora da malha (criado depois dela) fica sem lista.
        report.details["schools_listed"] = listed
        report.details["schools_without_territory"] = len(censuses[-1].schools) - listed
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
        description="Importa as escolas públicas do Censo Escolar do INEP."
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
