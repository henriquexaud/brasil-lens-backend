from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

BBox = tuple[float, float, float, float]


async def fetch_version(session: AsyncSession) -> int:
    """Última ingestão concluída da malha; muda a cada importação e vira chave de cache."""
    result = await session.execute(
        text(
            "SELECT COALESCE(MAX(id), 0) FROM ingestion_runs "
            "WHERE job = 'import_transport' AND status = 'succeeded'"
        )
    )
    return int(result.scalar_one())


async def fetch_run_details(session: AsyncSession, run_id: int) -> dict[str, Any]:
    result = await session.execute(
        text("SELECT details FROM ingestion_runs WHERE id = :run_id"), {"run_id": run_id}
    )
    return result.scalar_one_or_none() or {}


async def fetch_overview(session: AsyncSession, layer: str, digits: int) -> str | None:
    result = await session.execute(
        text("SELECT ST_AsGeoJSON(geom, :digits) FROM transport_overviews WHERE layer = :layer"),
        {"layer": layer, "digits": digits},
    )
    return result.scalar_one_or_none()


async def fetch_lines(
    session: AsyncSession, layer: str, bbox: BBox, tolerance: float, digits: int
) -> str | None:
    """Os trechos da área numa só multilinha: um path no mapa, não milhares."""
    result = await session.execute(
        text(
            """
            WITH area AS (SELECT ST_MakeEnvelope(:west, :south, :east, :north, 4326) AS geom)
            SELECT ST_AsGeoJSON(ST_Simplify(ST_Collect(part.geom), :tolerance, true), :digits)
            FROM transport_features f
            CROSS JOIN area
            CROSS JOIN LATERAL ST_Dump(ST_ClipByBox2D(f.geom, area.geom)) AS part
            WHERE f.layer = :layer AND f.geom && area.geom AND ST_Dimension(part.geom) = 1
            """
        ),
        {
            "layer": layer,
            "west": bbox[0],
            "south": bbox[1],
            "east": bbox[2],
            "north": bbox[3],
            "tolerance": tolerance,
            "digits": digits,
        },
    )
    return result.scalar_one_or_none()


async def fetch_points(session: AsyncSession, layer: str) -> list[Row[Any]]:
    result = await session.execute(
        text(
            "SELECT id, name, attributes, ST_AsGeoJSON(geom, 5) AS geometry "
            "FROM transport_features WHERE layer = :layer ORDER BY id"
        ),
        {"layer": layer},
    )
    return list(result.all())


async def identify(
    session: AsyncSession, layer: str, longitude: float, latitude: float, tolerance: float
) -> Row[Any] | None:
    result = await session.execute(
        text(
            """
            WITH point AS (SELECT ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326) AS geom)
            SELECT f.layer, f.name, f.attributes
            FROM transport_features f CROSS JOIN point
            WHERE f.layer = :layer AND ST_DWithin(f.geom, point.geom, :tolerance)
            ORDER BY f.geom <-> point.geom
            LIMIT 1
            """
        ),
        {"layer": layer, "longitude": longitude, "latitude": latitude, "tolerance": tolerance},
    )
    return result.first()


async def fetch_territory(session: AsyncSession, code: str) -> Row[Any] | None:
    result = await session.execute(
        text("SELECT id, ibge_code, name FROM territories WHERE ibge_code = :code"),
        {"code": code},
    )
    return result.first()


async def fetch_totals(session: AsyncSession, territory_id: int) -> dict[str, float]:
    result = await session.execute(
        text("SELECT layer, value FROM transport_totals WHERE territory_id = :territory_id"),
        {"territory_id": territory_id},
    )
    return {layer: float(value) for layer, value in result.all()}


async def fetch_sources(session: AsyncSession) -> dict[str, Row[Any]]:
    result = await session.execute(
        text(
            "SELECT source, name, url FROM datasets WHERE (source, code) IN "
            "(('dnit', 'snv/rodovias-federais'), ('ibge', 'bc250/transportes'))"
        )
    )
    return {row.source: row for row in result.all()}
