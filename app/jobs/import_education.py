"""Importa o contexto Educação: o Censo da Educação Superior do INEP (ADR-19).

Uma transação por execução, cobrindo todos os anos pedidos: se um ano falhar,
nada muda. Cada ano substitui por inteiro as instituições, as ofertas e as
matrículas dele, porque um município que deixou de ter oferta precisa perder o
valor (sem oferta não é zero, é ausência).
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
from app.jobs.import_indicators import persist
from app.jobs.seed_indicators import EDUCATION_CATALOG, RETIRED_EDUCATION_KEYS, seed_catalog
from app.models import EducationInstitution, Indicator, IndicatorValue
from app.providers import inep_higher_education as inep
from app.providers.ibge.indicator_records import IndicatorObservation

JOB_NAME = "import_education"
DEFAULT_YEARS = tuple(range(2019, 2025))
DEFAULT_CACHE = Path("/tmp/brasil-lens-inep")
COUNTRY_CODE = "BR"

INSERT_INSTITUTIONS_SQL = text("""
    INSERT INTO education_institutions
        (year, code, name, acronym, category, organization, seat_territory_id,
         faculty, staff, distance_enrollment, dataset_id)
    SELECT :year, source.code, source.name, source.acronym, source.category,
           source.organization, t.id, source.faculty, source.staff, source.distance,
           :dataset_id
      FROM unnest(CAST(:codes AS integer[]), CAST(:names AS text[]), CAST(:acronyms AS text[]),
                  CAST(:categories AS text[]), CAST(:organizations AS text[]),
                  CAST(:seats AS text[]), CAST(:faculty AS integer[]), CAST(:staff AS integer[]),
                  CAST(:distance AS integer[]))
           AS source(code, name, acronym, category, organization, seat, faculty, staff, distance)
      LEFT JOIN territories t ON t.ibge_code = source.seat
""")

INSERT_OFFERS_SQL = text("""
    INSERT INTO education_offers
        (year, institution_code, territory_id, courses, enrollment, entrants, graduates)
    SELECT :year, source.code, t.id, source.courses, source.enrollment, source.entrants,
           source.graduates
      FROM unnest(CAST(:codes AS integer[]), CAST(:municipalities AS text[]),
                  CAST(:courses AS integer[]), CAST(:enrollment AS integer[]),
                  CAST(:entrants AS integer[]), CAST(:graduates AS integer[]))
           AS source(code, municipality, courses, enrollment, entrants, graduates)
      JOIN territories t ON t.ibge_code = source.municipality AND t.level = 'municipality'
""")


def _scopes(code: str) -> tuple[str, ...]:
    """Município, UF, região e Brasil, pelos prefixos do código IBGE."""
    return (code, code[:2], code[:1], COUNTRY_CODE)


def enrollment_totals(census: inep.Census) -> dict[str, dict[str, int]]:
    """Matrículas por indicador e território: a rede inteira e a rede por tipo.

    Só entra o território que tem oferta; os indicadores sem nenhuma ficam vazios.
    """
    totals: dict[str, dict[str, int]] = {seed.key: {} for seed in EDUCATION_CATALOG}
    for (code, municipality), offer in census.offers.items():
        institution = census.institutions[code]
        network = institution.category
        kind = inep.KINDS[institution.organization]
        for key in (
            f"higher_ed_{network}_enrollment",
            f"higher_ed_{network}_{kind}_enrollment",
        ):
            for scope in _scopes(municipality):
                totals[key][scope] = totals[key].get(scope, 0) + offer.enrollment
    return totals


def _observations(values: dict[str, int], year: int) -> list[IndicatorObservation]:
    return [
        IndicatorObservation(ibge_code=code, reference_year=year, value=Decimal(value))
        for code, value in sorted(values.items())
    ]


async def _replace_year(
    session: AsyncSession,
    census: inep.Census,
    *,
    dataset_id: int,
    run_id: int,
    indicators: dict[str, int],
) -> dict[str, int]:
    year = census.year
    await session.execute(delete(EducationInstitution).where(EducationInstitution.year == year))
    institutions = list(census.institutions.values())
    await session.execute(
        INSERT_INSTITUTIONS_SQL,
        {
            "year": year,
            "dataset_id": dataset_id,
            "codes": [item.code for item in institutions],
            "names": [item.name for item in institutions],
            "acronyms": [item.acronym for item in institutions],
            "categories": [item.category for item in institutions],
            "organizations": [item.organization for item in institutions],
            "seats": [item.seat_code for item in institutions],
            "faculty": [item.faculty for item in institutions],
            "staff": [item.staff for item in institutions],
            "distance": [census.distance_enrollment.get(item.code, 0) for item in institutions],
        },
    )
    offers = list(census.offers.items())
    inserted = affected_rows(
        await session.execute(
            INSERT_OFFERS_SQL,
            {
                "year": year,
                "codes": [code for (code, _), _ in offers],
                "municipalities": [municipality for (_, municipality), _ in offers],
                "courses": [offer.courses for _, offer in offers],
                "enrollment": [offer.enrollment for _, offer in offers],
                "entrants": [offer.entrants for _, offer in offers],
                "graduates": [offer.graduates for _, offer in offers],
            },
        )
    )
    written = 0
    for key, values in enrollment_totals(census).items():
        indicator_id = indicators[key]
        await session.execute(
            delete(IndicatorValue).where(
                IndicatorValue.indicator_id == indicator_id, IndicatorValue.reference_year == year
            )
        )
        written += await persist(
            session,
            _observations(values, year),
            indicator_id=indicator_id,
            dataset_id=dataset_id,
            run_id=run_id,
        )
    return {
        "institutions": len(institutions),
        "offers": len(offers),
        # Município fora da malha (raro: criado depois dela) não entra no mapa.
        "offers_without_territory": len(offers) - inserted,
        "values": written,
    }


async def ingest(years: Iterable[int], cache_dir: Path) -> int:
    years = sorted(set(years))
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Tudo é baixado e lido antes de abrir a transação: parada minutos à espera do
    # zip de 2024, a conexão era derrubada pelo Neon no meio da ingestão.
    async with inep.client() as client:
        # Sequencial: um zip por ano, de até ~460 MB, lido em streaming.
        censuses = [
            inep.read_census(await inep.download(client, year, cache_dir), year) for year in years
        ]
    async with job_session(job=JOB_NAME, source="inep") as (session, run_id, report):
        await session.execute(delete(Indicator).where(Indicator.key.in_(RETIRED_EDUCATION_KEYS)))
        await seed_catalog(session, EDUCATION_CATALOG)
        rows = await session.execute(
            select(Indicator.key, Indicator.id).where(Indicator.context == "education")
        )
        indicators = {key: identifier for key, identifier in rows.all()}
        dataset_id = await upsert_dataset(
            session,
            source="inep",
            code="censo-superior/microdados",
            name="INEP — Censo da Educação Superior, microdados de instituições e cursos",
            url=inep.DATASET_URL,
        )
        for census in censuses:
            details = await _replace_year(
                session, census, dataset_id=dataset_id, run_id=run_id, indicators=indicators
            )
            report.processed += details["offers"]
            report.written += details["values"]
            report.details[str(census.year)] = details
        await session.commit()
    _print(report)
    return 0


def _print(report: RunReport) -> None:
    print(
        f"Educação importada: {report.processed} ofertas por município, "
        f"{report.written} valores gravados."
    )


def _years(value: str) -> list[int]:
    try:
        if "-" in value:
            start, end = (int(part) for part in value.split("-", 1))
            return list(range(start, end + 1))
        return [int(part) for part in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use anos como 2024, 2022,2024 ou 2019-2024.") from exc


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Importa as instituições públicas de ensino superior do Censo do INEP."
    )
    parser.add_argument(
        "--years",
        type=_years,
        default=list(DEFAULT_YEARS),
        help="Anos do Censo (ex.: 2024, 2022,2024 ou 2019-2024). Padrão: 2019-2024.",
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
