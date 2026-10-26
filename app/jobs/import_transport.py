"""Importa a malha de transporte: rodovias, ferrovias, hidrovias e portos (ADR-17).

Federais vêm do SNV do DNIT (versão mais recente); estaduais, locais,
ferrovias, hidrovias e portos, do BC250 2025 do IBGE. Uma transação por
execução substitui a malha inteira: se uma fonte falhar, a anterior fica.
Depois da carga, a mesma transação funde um traçado nacional por camada e
soma a extensão (km) e os portos de cada município, UF, região e Brasil.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterable
from pathlib import Path

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs._runner import job_session, run_job, upsert_dataset
from app.providers import bc250, dnit
from app.providers.base import http_client
from app.providers.bc250 import TransportFeature

JOB_NAME = "import_transport"
BATCH_SIZE = 2000
# ~30 m: abaixo do erro de posição de uma carta 1:250.000 (~125 m). Com 0,0001 a
# malha ocupava 64 MB no banco, sem diferença visível no mapa.
STORAGE_TOLERANCE = 0.0003
# Traçado nacional (zoom < 6): ~1 km, invisível nessa escala.
OVERVIEW_TOLERANCE = 0.01
# Estaduais e locais só aparecem de perto (`services/transport.LAYERS`).
OVERVIEW_LAYERS = ("road_federal", "rail", "waterway")
# Porto fica na água: conta para o município mais próximo, até ~5 km.
PORT_DISTANCE = 0.05

_INSERT = text(
    "INSERT INTO transport_features (layer, name, attributes, geom, dataset_id) "
    "VALUES (:layer, :name, CAST(:attributes AS jsonb), "
    f"ST_Simplify(ST_GeomFromWKB(:geometry, 4326), {STORAGE_TOLERANCE}, true), :dataset_id)"
)

_OVERVIEW = text(
    "INSERT INTO transport_overviews (layer, geom) "
    f"SELECT layer, ST_Simplify(ST_LineMerge(ST_Collect(part.geom)), {OVERVIEW_TOLERANCE}, true) "
    "FROM transport_features CROSS JOIN LATERAL ST_Dump(geom) AS part "
    "WHERE layer = ANY(:layers) GROUP BY layer"
)

_TOTALS = text(
    f"""
    INSERT INTO transport_totals (territory_id, layer, value)
    WITH municipalities AS (
        SELECT t.ibge_code, g.geom
        FROM territories t
        JOIN territory_geometries g ON g.territory_id = t.id AND g.lod = 'detail'
        WHERE t.level = 'municipality'
    ),
    lengths AS (
        SELECT m.ibge_code, f.layer,
               SUM(ST_Length(ST_Intersection(f.geom, m.geom)::geography)) / 1000 AS value
        FROM transport_features f
        JOIN municipalities m ON ST_Intersects(f.geom, m.geom)
        WHERE f.layer <> 'port'
        GROUP BY m.ibge_code, f.layer
    ),
    ports AS (
        SELECT nearest.ibge_code, 'port' AS layer, COUNT(*)::float8 AS value
        FROM transport_features f
        CROSS JOIN LATERAL (
            SELECT m.ibge_code FROM municipalities m
            WHERE ST_DWithin(m.geom, f.geom, {PORT_DISTANCE})
            ORDER BY m.geom <-> f.geom LIMIT 1
        ) nearest
        WHERE f.layer = 'port'
        GROUP BY nearest.ibge_code
    ),
    scoped AS (
        SELECT scope.code, item.layer, SUM(item.value) AS value
        FROM (SELECT * FROM lengths UNION ALL SELECT * FROM ports) item
        CROSS JOIN LATERAL (
            VALUES (item.ibge_code), (LEFT(item.ibge_code, 2)),
                   (LEFT(item.ibge_code, 1)), ('BR')
        ) AS scope(code)
        GROUP BY scope.code, item.layer
    )
    SELECT t.id, s.layer, ROUND(s.value::numeric, 1)
    FROM scoped s JOIN territories t ON t.ibge_code = s.code
    """
)


async def _insert(
    session: AsyncSession, features: Iterable[TransportFeature], dataset_id: int
) -> dict[str, int]:
    counts: dict[str, int] = {}
    batch: list[dict[str, object]] = []
    for feature in features:
        counts[feature.layer] = counts.get(feature.layer, 0) + 1
        batch.append(
            {
                "layer": feature.layer,
                "name": feature.name[:160] if feature.name else None,
                "attributes": orjson.dumps(feature.attributes).decode(),
                "geometry": feature.geometry,
                "dataset_id": dataset_id,
            }
        )
        if len(batch) == BATCH_SIZE:
            await session.execute(_INSERT, batch)
            batch = []
    if batch:
        await session.execute(_INSERT, batch)
    return counts


async def ingest(bc250_file: Path | None) -> int:
    # O GeoServer do DNIT leva ~1 min por página: busca sequencial, timeout largo.
    async with http_client(timeout=300) as client:
        version = await dnit.latest_version(client)
        federal = await dnit.fetch_roads(client, version)
    layers = await asyncio.to_thread(bc250.download_layers, bc250_file)

    async with job_session(job=JOB_NAME, source="dnit+ibge") as (session, run_id, report):
        snv_dataset = await upsert_dataset(
            session,
            source="dnit",
            code="snv/rodovias-federais",
            name=f"DNIT — Sistema Nacional de Viação (SNV), versão {version}",
            url=dnit.DATASET_URL,
        )
        bc250_dataset = await upsert_dataset(
            session,
            source="ibge",
            code="bc250/transportes",
            name=f"IBGE — Base Cartográfica Contínua 1:250.000 (BC250), versão {bc250.EDITION}",
            url=bc250.DATASET_URL,
        )
        for table in ("transport_totals", "transport_overviews", "transport_features"):
            await session.execute(text(f"DELETE FROM {table}"))
        counts = await _insert(session, dnit.roads(federal), snv_dataset)
        for layer, count in (await _insert(session, bc250.features(layers), bc250_dataset)).items():
            counts[layer] = counts.get(layer, 0) + count
        await session.execute(_OVERVIEW, {"layers": list(OVERVIEW_LAYERS)})
        await session.execute(_TOTALS)
        totals = await session.execute(text("SELECT COUNT(*) FROM transport_totals"))
        report.processed = len(federal) + sum(counts.values())
        report.written = sum(counts.values())
        report.details = {
            "snv_version": version,
            "bc250": bc250.EDITION,
            "features": counts,
            "totals": totals.scalar_one(),
            "file": bc250_file.name if bc250_file else None,
        }
        await session.commit()
    print(f"Transporte importado (SNV {version}, BC250 {bc250.EDITION}): {counts}.")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Importa rodovias (DNIT/IBGE), ferrovias, hidrovias e portos (IBGE)."
    )
    parser.add_argument(
        "--bc250-file",
        type=Path,
        help="Zip de shapefiles do BC250 baixado à mão, no lugar da leitura remota.",
    )
    args = parser.parse_args()
    if args.bc250_file is not None and not args.bc250_file.is_file():
        parser.error(f"Arquivo não encontrado: {args.bc250_file}")
    return await ingest(args.bc250_file)


if __name__ == "__main__":
    run_job(main)
