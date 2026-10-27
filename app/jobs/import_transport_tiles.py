"""Grava no banco os tiles vetoriais de transporte do OpenStreetMap (ADR-18).

O `.mbtiles` sai de `scripts/transport_tiles/build.sh` (Planetiler sobre o
extrato da Geofabrik, recortado à fronteira do IBGE). Os tiles já vêm
comprimidos em gzip e são gravados como estão, numa transação: a API só os lê
pela chave. O MBTiles numera as linhas de baixo para cima (TMS); o mapa, de
cima para baixo (XYZ).
"""

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.jobs._runner import job_session, run_job, upsert_dataset

JOB_NAME = "import_transport_tiles"
BATCH_SIZE = 1000
DATASET_URL = "https://download.geofabrik.de/south-america/brazil.html"

_INSERT = text("INSERT INTO transport_tiles (zoom, x, y, data) VALUES (:zoom, :x, :y, :data)")


def read_metadata(connection: sqlite3.Connection) -> dict[str, str]:
    return dict(connection.execute("SELECT name, value FROM metadata").fetchall())


def tile_batches(
    connection: sqlite3.Connection, size: int = BATCH_SIZE
) -> Iterator[list[dict[str, Any]]]:
    """Os tiles do MBTiles em lotes, já na numeração XYZ do mapa."""
    rows = connection.execute("SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles")
    while batch := rows.fetchmany(size):
        yield [
            {"zoom": zoom, "x": x, "y": (1 << zoom) - 1 - row, "data": data}
            for zoom, x, row, data in batch
        ]


def osm_date(metadata: dict[str, str]) -> str | None:
    """Data do extrato (replicação do OSM), gravada pelo Planetiler nos metadados."""
    stamp = metadata.get("planetiler:osm:osmosisreplicationtime", "")
    # Sem cabeçalho de replicação, o Planetiler grava a época Unix.
    return stamp[:10] if stamp and not stamp.startswith("1970") else None


async def ingest(mbtiles: Path) -> int:
    connection = sqlite3.connect(f"file:{mbtiles}?mode=ro", uri=True)
    try:
        metadata = read_metadata(connection)
        if metadata.get("format") != "pbf":
            raise SystemExit(f"Esperava tiles vetoriais (pbf), veio {metadata.get('format')!r}.")
        async with job_session(job=JOB_NAME, source="osm") as (session, run_id, report):
            await upsert_dataset(
                session,
                source="osm",
                code="geofabrik/brasil-transporte",
                name="OpenStreetMap — vias, ciclovias, ferrovias, estações e balsas do Brasil "
                "(extrato da Geofabrik, ODbL)",
                url=DATASET_URL,
            )
            await session.execute(text("DELETE FROM transport_tiles"))
            size = 0
            for batch in tile_batches(connection):
                await session.execute(_INSERT, batch)
                report.written += len(batch)
                size += sum(len(item["data"]) for item in batch)
            report.processed = report.written
            report.details = {
                "file": mbtiles.name,
                "bytes": size,
                "minzoom": int(metadata.get("minzoom", 0)),
                "maxzoom": int(metadata.get("maxzoom", 0)),
                "bounds": metadata.get("bounds"),
                "osm_date": osm_date(metadata),
                "compression": "gzip",
            }
            await session.commit()
    finally:
        connection.close()
    print(f"Tiles de transporte importados: {report.written} tiles, {size / 1e6:.0f} MB.")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="Grava os tiles OSM de transporte no banco.")
    parser.add_argument("--mbtiles", type=Path, required=True, help="Saída do build.sh.")
    args = parser.parse_args()
    if not args.mbtiles.is_file():
        parser.error(f"Arquivo não encontrado: {args.mbtiles}")
    return await ingest(args.mbtiles)


if __name__ == "__main__":
    run_job(main)
