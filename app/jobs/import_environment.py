"""Importa o contexto Meio ambiente: desmatamento (PRODES/INPE) e florestas (MapBiomas).

Uma transação por execução: se uma fonte falhar, a série anterior fica intacta.
Os totais de UF e Brasil somam os municípios publicados pela fonte, que cobre
os biomas inteiros; por isso um município criado depois da malha da fonte
fica sem dado, sem desfalcar o total da UF.
"""

from __future__ import annotations

import argparse
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime, time
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs._runner import RunReport, job_session, run_job, upsert_dataset
from app.jobs.import_indicators import persist
from app.jobs.seed_indicators import ENVIRONMENT_CATALOG, seed_catalog
from app.models import Indicator
from app.providers import mapbiomas, prodes
from app.providers.base import http_client
from app.providers.ibge.indicator_records import IndicatorObservation

JOB_NAME = "import_environment"
SOURCES = ("prodes", "mapbiomas")
COUNTRY_CODE = "BR"


def _observations(values: dict[tuple[str, int], Decimal]) -> list[IndicatorObservation]:
    return [
        IndicatorObservation(ibge_code=code, reference_year=year, value=value)
        for (code, year), value in sorted(values.items())
    ]


def _scopes(code: str) -> tuple[str, ...]:
    """Município, UF, região e Brasil, pelos prefixos do código IBGE."""
    return (code, code[:2], code[:1], COUNTRY_CODE)


def deforestation_totals(
    records: Iterable[prodes.DeforestationRecord],
) -> dict[tuple[str, int], Decimal]:
    totals: dict[tuple[str, int], Decimal] = {}
    for record in records:
        for code in _scopes(record.ibge_code):
            key = (code, record.reference_year)
            totals[key] = totals.get(key, Decimal(0)) + record.value
    return totals


def forest_cover(areas: Iterable[mapbiomas.ForestArea]) -> dict[tuple[str, int], Decimal]:
    sums: dict[tuple[str, int], tuple[Decimal, Decimal]] = {}
    for item in areas:
        for code in _scopes(item.ibge_code):
            key = (code, item.reference_year)
            forest, total = sums.get(key, (Decimal(0), Decimal(0)))
            sums[key] = (forest + item.forest_ha, total + item.total_ha)
    return {
        key: (forest * 100 / total).quantize(Decimal("0.0001"))
        for key, (forest, total) in sums.items()
        if total > 0
    }


async def _indicator_ids(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(select(Indicator.key, Indicator.id))
    return {key: identifier for key, identifier in rows.all()}


async def import_prodes(session: AsyncSession, run_id: int, report: RunReport) -> None:
    async with http_client(timeout=120) as client:
        # Sequencial: são 12 arquivos estáticos, sem pressa e sem rajada na fonte.
        biomes = [await prodes.fetch_biome(client, biome) for biome in prodes.BIOMES]
        updated = await prodes.fetch_last_update(client)
    records = prodes.combine(biomes)
    totals = deforestation_totals(records)
    dataset_id = await upsert_dataset(
        session,
        source="inpe",
        code="prodes/municipal-increments",
        name="INPE — PRODES, incremento anual de desmatamento por município (TerraBrasilis)",
        url=prodes.DASHBOARD_URL,
        source_updated_at=datetime.combine(updated, time(), UTC) if updated else None,
    )
    ids = await _indicator_ids(session)
    written = await persist(
        session,
        _observations(totals),
        indicator_id=ids["deforestation_prodes"],
        dataset_id=dataset_id,
        run_id=run_id,
    )
    years = sorted({record.reference_year for record in records})
    report.processed += len(records)
    report.written += written
    report.details["prodes"] = {"municipal_records": len(records), "years": years}


async def import_mapbiomas(
    session: AsyncSession, run_id: int, report: RunReport, file: Path | None
) -> None:
    with tempfile.TemporaryDirectory(prefix="mapbiomas-") as directory:
        if file is None:
            async with http_client(timeout=600) as client:
                xlsx = await mapbiomas.download_coverage(client, Path(directory))
        else:
            xlsx = mapbiomas.extract_xlsx(file, Path(directory))
        areas = mapbiomas.read_forest_areas(xlsx)
    shares = forest_cover(areas)
    dataset_id = await upsert_dataset(
        session,
        source="mapbiomas",
        code="colecao-11/cobertura-municipios",
        name="MapBiomas Coleção 11 — Cobertura e uso da terra por município",
        url=mapbiomas.DATASET_URL,
    )
    ids = await _indicator_ids(session)
    written = await persist(
        session,
        _observations(shares),
        indicator_id=ids["forest_cover"],
        dataset_id=dataset_id,
        run_id=run_id,
    )
    report.processed += len(areas)
    report.written += written
    report.details["mapbiomas"] = {
        "municipal_records": len(areas),
        "years": sorted({item.reference_year for item in areas}),
        "file": file.name if file else None,
    }


async def ingest(sources: tuple[str, ...], mapbiomas_file: Path | None) -> int:
    async with job_session(job=JOB_NAME, source="+".join(sources)) as (session, run_id, report):
        await seed_catalog(session, ENVIRONMENT_CATALOG)
        if "prodes" in sources:
            await import_prodes(session, run_id, report)
        if "mapbiomas" in sources:
            await import_mapbiomas(session, run_id, report, mapbiomas_file)
        await session.commit()
    print(
        f"Meio ambiente importado: {report.processed} registros, "
        f"{report.written} valores gravados."
    )
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Importa desmatamento (PRODES/INPE) e florestas (MapBiomas) por município."
    )
    parser.add_argument(
        "--only", choices=SOURCES, help="Importa só uma das fontes. Padrão: as duas."
    )
    parser.add_argument(
        "--mapbiomas-file",
        type=Path,
        help="Zip ou xlsx da Coleção 11 baixado à mão, no lugar do download do Drive.",
    )
    args = parser.parse_args()
    if args.mapbiomas_file is not None and not args.mapbiomas_file.is_file():
        parser.error(f"Arquivo não encontrado: {args.mapbiomas_file}")
    sources = (args.only,) if args.only else SOURCES
    return await ingest(sources, args.mapbiomas_file)


if __name__ == "__main__":
    run_job(main)
