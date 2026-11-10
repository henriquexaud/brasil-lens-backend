from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

BBox = tuple[float, float, float, float]

# Uma linha por categoria do lugar: a principal (mesmo nula) e as demais.
_EVERY_CATEGORY = (
    "CROSS JOIN LATERAL unnest(array_prepend(p.category, "
    "COALESCE(p.extra_categories, CAST('{}' AS varchar(32)[])))) AS c(category)"
)


async def fetch_version(session: AsyncSession) -> int:
    """Última ingestão concluída de qualquer camada; vira chave de cache."""
    result = await session.execute(
        text(
            "SELECT COALESCE(MAX(id), 0) FROM ingestion_runs "
            "WHERE job = 'import_places' AND status = 'succeeded'"
        )
    )
    return int(result.scalar_one())


async def fetch_layer_runs(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """Detalhes (contagem, edição) da última ingestão concluída de cada camada."""
    result = await session.execute(
        text(
            "SELECT DISTINCT ON (details->>'layer') details FROM ingestion_runs "
            "WHERE job = 'import_places' AND status = 'succeeded' AND details ? 'layer' "
            "ORDER BY details->>'layer', id DESC"
        )
    )
    return {row.details["layer"]: row.details for row in result.all()}


async def fetch_datasets(session: AsyncSession, codes: list[str]) -> dict[str, Row[Any]]:
    result = await session.execute(
        text("SELECT code, name, url FROM datasets WHERE code = ANY(:codes)"), {"codes": codes}
    )
    return {row.code: row for row in result.all()}


async def fetch_category_counts(session: AsyncSession, context: str) -> list[Row[Any]]:
    result = await session.execute(
        text(
            "SELECT p.layer, c.category, COUNT(*) AS total FROM places p "
            f"{_EVERY_CATEGORY} WHERE p.context = :context GROUP BY p.layer, c.category"
        ),
        {"context": context},
    )
    return list(result.all())


async def fetch_layer_counts(session: AsyncSession, context: str) -> dict[str, int]:
    """Lugares por camada; a soma das categorias contaria duas vezes o lugar de várias."""
    result = await session.execute(
        text("SELECT layer, COUNT(*) AS total FROM places WHERE context = :context GROUP BY layer"),
        {"context": context},
    )
    return {row.layer: int(row.total) for row in result.all()}


async def fetch_values(
    session: AsyncSession, layer: str, category: str | None, length: int, parent: str | None
) -> dict[str, int]:
    result = await session.execute(
        text(
            """
            SELECT LEFT(municipality_code, :length) AS code, COUNT(*) AS total
            FROM places
            WHERE layer = :layer AND municipality_code IS NOT NULL
              AND (CAST(:category AS text) IS NULL OR category = :category
                   OR :category = ANY(extra_categories))
              AND (CAST(:parent AS text) IS NULL OR municipality_code LIKE :parent || '%')
            GROUP BY 1
            """
        ),
        {"layer": layer, "category": category, "length": length, "parent": parent},
    )
    return {row.code: int(row.total) for row in result.all()}


async def fetch_points(
    session: AsyncSession, layer: str, category: str | None, bbox: BBox, limit: int
) -> list[Row[Any]]:
    result = await session.execute(
        text(
            """
            SELECT id, name, category, ST_X(point) AS longitude, ST_Y(point) AS latitude
            FROM places
            WHERE layer = :layer AND point && ST_MakeEnvelope(:west, :south, :east, :north, 4326)
              AND (CAST(:category AS text) IS NULL OR category = :category
                   OR :category = ANY(extra_categories))
            ORDER BY weight DESC NULLS LAST, id
            LIMIT :limit
            """
        ),
        {
            "layer": layer,
            "category": category,
            "west": bbox[0],
            "south": bbox[1],
            "east": bbox[2],
            "north": bbox[3],
            "limit": limit,
        },
    )
    return list(result.all())


async def fetch_areas(
    session: AsyncSession,
    layer: str,
    category: str | None,
    bbox: BBox | None,
    tolerance: float,
    digits: int,
    min_size: float,
) -> list[Row[Any]]:
    """Contornos simplificados para a escala; com área, recortados a ela."""
    result = await session.execute(
        text(
            """
            WITH area AS (
                SELECT CASE WHEN CAST(:west AS float8) IS NOT NULL
                       THEN ST_MakeEnvelope(:west, :south, :east, :north, 4326) END AS geom
            ),
            shaped AS (
                SELECT p.id, p.name, p.category, p.weight,
                       ST_SimplifyPreserveTopology(
                           CASE WHEN area.geom IS NULL THEN p.shape
                                ELSE ST_ClipByBox2D(p.shape, area.geom) END,
                           :tolerance) AS geom
                FROM places p CROSS JOIN area
                WHERE p.layer = :layer AND p.shape IS NOT NULL
                  AND (area.geom IS NULL OR p.shape && area.geom)
                  AND (CAST(:category AS text) IS NULL OR p.category = :category
                       OR :category = ANY(p.extra_categories))
                  AND GREATEST(ST_XMax(p.shape) - ST_XMin(p.shape),
                               ST_YMax(p.shape) - ST_YMin(p.shape)) >= :min_size
            )
            SELECT id, name, category, ST_AsGeoJSON(geom, :digits) AS geometry
            FROM shaped
            WHERE geom IS NOT NULL AND NOT ST_IsEmpty(geom) AND ST_Dimension(geom) = 2
            ORDER BY weight DESC NULLS LAST, id
            """
        ),
        {
            "layer": layer,
            "category": category,
            "west": bbox[0] if bbox else None,
            "south": bbox[1] if bbox else None,
            "east": bbox[2] if bbox else None,
            "north": bbox[3] if bbox else None,
            "tolerance": tolerance,
            "digits": digits,
            "min_size": min_size,
        },
    )
    return list(result.all())


async def fetch_place(session: AsyncSession, place_id: int) -> Row[Any] | None:
    result = await session.execute(
        text(
            """
            SELECT p.id, p.layer, p.category, p.name, p.summary, p.attributes,
                   p.municipality_code, ST_X(p.point) AS longitude, ST_Y(p.point) AS latitude,
                   t.name AS municipality_name, s.abbreviation AS state
            FROM places p
            LEFT JOIN territories t ON t.ibge_code = p.municipality_code
            LEFT JOIN territories s ON s.id = t.parent_id
            WHERE p.id = :place_id
            """
        ),
        {"place_id": place_id},
    )
    return result.first()


async def fetch_territory(session: AsyncSession, code: str) -> Row[Any] | None:
    result = await session.execute(
        text(
            "SELECT id, ibge_code, name, level::text AS level FROM territories "
            "WHERE ibge_code = :code"
        ),
        {"code": code},
    )
    return result.first()


_MATCHES_CATEGORY = (
    "AND (CAST(:category AS text) IS NULL OR p.category = :category "
    "OR :category = ANY(p.extra_categories))"
)
_ITEM_ORDER = "ORDER BY p.weight DESC NULLS LAST, p.name NULLS LAST, p.id LIMIT :limit"
_ITEM_COLUMNS = (
    "p.id, p.name, p.category, p.summary, p.weight, "
    "ST_X(p.point) AS longitude, ST_Y(p.point) AS latitude"
)


async def fetch_territory_points(
    session: AsyncSession, layer: str, category: str | None, prefix: str, limit: int
) -> tuple[dict[str | None, int], int, list[Row[Any]]]:
    """Contagem por categoria, total do filtro e os primeiros lugares (por peso)."""
    params = {"layer": layer, "category": category, "prefix": prefix, "limit": limit}
    scope = "p.layer = :layer AND (:prefix = '' OR p.municipality_code LIKE :prefix || '%')"
    counts = await session.execute(
        text(
            f"SELECT c.category, COUNT(*) AS total FROM places p {_EVERY_CATEGORY} "
            f"WHERE {scope} GROUP BY 1"
        ),
        params,
    )
    matching = f"FROM places p WHERE {scope} {_MATCHES_CATEGORY}"
    total = await session.execute(text(f"SELECT COUNT(*) {matching}"), params)
    items = await session.execute(text(f"SELECT {_ITEM_COLUMNS} {matching} {_ITEM_ORDER}"), params)
    return (
        {row.category: int(row.total) for row in counts.all()},
        int(total.scalar_one()),
        list(items.all()),
    )


async def fetch_territory_areas(
    session: AsyncSession, layer: str, category: str | None, territory_id: int | None, limit: int
) -> tuple[dict[str | None, int], int, list[Row[Any]]]:
    """Áreas que entram no território (sem as que só encostam na divisa)."""
    params = {"layer": layer, "category": category, "territory_id": territory_id, "limit": limit}
    # A divisa encolhida (~200 m) tira os vizinhos; num território minúsculo ela some
    # e vale a original. Calculada uma vez por consulta.
    scope = """
        WITH inner_area AS MATERIALIZED (
            SELECT CASE WHEN ST_IsEmpty(ST_Buffer(g.geom, -0.002)) THEN g.geom
                        ELSE ST_Buffer(g.geom, -0.002) END AS geom
            FROM territory_geometries g
            WHERE g.territory_id = :territory_id AND g.lod = 'overview'
        )
    """
    source = """
        FROM places p
        WHERE p.layer = :layer AND p.shape IS NOT NULL
          AND (CAST(:territory_id AS integer) IS NULL OR EXISTS (
              SELECT 1 FROM inner_area
              WHERE p.shape && inner_area.geom AND ST_Intersects(p.shape, inner_area.geom)
          ))
    """
    counts = await session.execute(
        text(
            f"{scope} SELECT c.category, COUNT(*) AS total "
            f"{source.replace('WHERE p.layer', _EVERY_CATEGORY + ' WHERE p.layer', 1)} GROUP BY 1"
        ),
        params,
    )
    total = await session.execute(
        text(f"{scope} SELECT COUNT(*) {source} {_MATCHES_CATEGORY}"), params
    )
    items = await session.execute(
        text(f"{scope} SELECT {_ITEM_COLUMNS} {source} {_MATCHES_CATEGORY} {_ITEM_ORDER}"), params
    )
    return (
        {row.category: int(row.total) for row in counts.all()},
        int(total.scalar_one()),
        list(items.all()),
    )


async def fetch_biomes(session: AsyncSession, code: str) -> list[Row[Any]]:
    result = await session.execute(
        text(
            "SELECT biome, share FROM territory_biomes WHERE territory_code = :code "
            "ORDER BY share DESC"
        ),
        {"code": code},
    )
    return list(result.all())


async def fetch_reference_point(session: AsyncSession, territory_id: int) -> Row[Any] | None:
    """Um ponto dentro do território, para achar a unidade correspondente no GBIF."""
    result = await session.execute(
        text(
            "SELECT ST_Y(ST_PointOnSurface(geom)) AS latitude, "
            "ST_X(ST_PointOnSurface(geom)) AS longitude "
            "FROM territory_geometries WHERE territory_id = :territory_id AND lod = 'overview'"
        ),
        {"territory_id": territory_id},
    )
    return result.first()
