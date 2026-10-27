"""Grava no banco os tiles vetoriais de transporte do OpenStreetMap (ADR-18).

O `.mbtiles` sai de `scripts/transport_tiles/build.sh` (Planetiler sobre o
extrato da Geofabrik, recortado à fronteira do IBGE). Os tiles já vêm
comprimidos em gzip e são gravados como estão: a API só os lê pela chave. O
MBTiles numera as linhas de baixo para cima (TMS); o mapa, de cima para baixo (XYZ).

São ~300 MB, longos demais para uma transação só pela rede até o Neon: uma queda
de conexão desfazia tudo. Os lotes vão para `transport_tiles_next`, com commit e
nova tentativa por lote; rodar de novo retoma do que já foi gravado. A troca para
`transport_tiles` é uma transação curta, feita dentro do banco.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.logging import get_logger
from app.db.session import SessionFactory
from app.jobs._runner import job_session, run_job, upsert_dataset

logger = get_logger(__name__)

JOB_NAME = "import_transport_tiles"
BATCH_SIZE = 1000
DATASET_URL = "https://download.geofabrik.de/south-america/brazil.html"

STAGING = "transport_tiles_next"
ATTEMPTS = 5

_INSERT = text(
    f"INSERT INTO {STAGING} (zoom, x, y, data) VALUES (:zoom, :x, :y, :data) "
    "ON CONFLICT DO NOTHING"
)


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


def fingerprint(mbtiles: Path, metadata: dict[str, str]) -> str:
    """Identifica o arquivo: o preparo de outro `.mbtiles` não pode ser retomado."""
    stamp = metadata.get("planetiler:osm:osmosisreplicationtime", "")
    return f"{mbtiles.stat().st_size}:{stamp}"


async def _prepare_staging(mark: str) -> set[tuple[int, int, int]]:
    """Cria ou retoma o preparo; devolve as chaves já gravadas deste mesmo arquivo."""
    async with SessionFactory() as session:
        await session.execute(
            text(f"CREATE TABLE IF NOT EXISTS {STAGING} (LIKE transport_tiles INCLUDING ALL)")
        )
        current = (
            await session.execute(text(f"SELECT obj_description('{STAGING}'::regclass)"))
        ).scalar_one()
        if current != mark:
            await session.execute(text(f"TRUNCATE {STAGING}"))
            await session.execute(text(f"COMMENT ON TABLE {STAGING} IS '{mark}'"))
            await session.commit()
            return set()
        rows = await session.execute(text(f"SELECT zoom, x, y FROM {STAGING}"))
        done = {(zoom, x, y) for zoom, x, y in rows}
        await session.commit()
    return done


async def _write(batch: list[dict[str, Any]]) -> None:
    for attempt in range(1, ATTEMPTS + 1):
        try:
            async with SessionFactory() as session:
                await session.execute(_INSERT, batch)
                await session.commit()
            return
        except (DBAPIError, OSError) as exc:
            if attempt == ATTEMPTS:
                raise
            logger.warning(
                "transport_tiles.batch_retry", extra={"attempt": attempt, "error": str(exc)}
            )
            await asyncio.sleep(2**attempt)


async def ingest(mbtiles: Path) -> int:
    connection = sqlite3.connect(f"file:{mbtiles}?mode=ro", uri=True)
    try:
        metadata = read_metadata(connection)
        if metadata.get("format") != "pbf":
            raise SystemExit(f"Esperava tiles vetoriais (pbf), veio {metadata.get('format')!r}.")
        done = await _prepare_staging(fingerprint(mbtiles, metadata))
        if done:
            print(f"Retomando: {len(done)} tiles já gravados no preparo.")
        total = size = 0
        for batch in tile_batches(connection):
            total += len(batch)
            size += sum(len(item["data"]) for item in batch)
            pending = [item for item in batch if (item["zoom"], item["x"], item["y"]) not in done]
            if pending:
                await _write(pending)
            if total % 20_000 < BATCH_SIZE:
                print(f"  {total} tiles…", flush=True)

        async with job_session(job=JOB_NAME, source="osm") as (session, run_id, report):
            await upsert_dataset(
                session,
                source="osm",
                code="geofabrik/brasil-transporte",
                name="OpenStreetMap — vias, ciclovias, ferrovias, estações e balsas do Brasil "
                "(extrato da Geofabrik, ODbL)",
                url=DATASET_URL,
            )
            staged = (await session.execute(text(f"SELECT count(*) FROM {STAGING}"))).scalar_one()
            if staged != total:
                raise RuntimeError(f"Preparo com {staged} tiles; o arquivo tem {total}.")
            await session.execute(text("DELETE FROM transport_tiles"))
            await session.execute(text(f"INSERT INTO transport_tiles SELECT * FROM {STAGING}"))
            await session.execute(text(f"DROP TABLE {STAGING}"))
            report.processed = report.written = total
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
    print(f"Tiles de transporte importados: {total} tiles, {size / 1e6:.0f} MB.")
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
