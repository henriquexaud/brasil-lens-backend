"""Importa os lugares dos contextos Natureza, Recursos e Esportes (ADR-25).

Cada camada é lida da fonte oficial e regravada inteira na própria transação:
se a fonte falhar, a camada anterior fica. Depois da carga, cada ponto ganha o
código IBGE do município que o contém, e os biomas, a parte de cada município e UF.
"""

from __future__ import annotations

import argparse
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs._runner import job_session, run_job, upsert_dataset
from app.providers import nature_places, ogc, osm_sports, resource_places
from app.providers.base import http_client
from app.providers.place_record import PlaceRecord

JOB_NAME = "import_places"


@dataclass(frozen=True, slots=True)
class LayerSource:
    layer: str
    context: str
    source: str
    code: str
    name: str
    url: str
    edition: str | None = None
    # Tolerância (graus) do contorno guardado; `None` guarda só o ponto.
    shape_tolerance: float | None = None
    batch_size: int = 500


SOURCES: tuple[LayerSource, ...] = (
    LayerSource(
        "biome",
        "nature",
        "inpe",
        "terrabrasilis/biomas",
        "IBGE — Biomas do Brasil 1:250.000, servidos pelo TerraBrasilis (INPE)",
        nature_places.BIOMES_DATASET_URL,
        shape_tolerance=0.002,
        batch_size=1,
    ),
    LayerSource(
        "conservation_unit",
        "nature",
        "mma",
        "cnuc/unidades-de-conservacao",
        "MMA — Cadastro Nacional de Unidades de Conservação (CNUC)",
        nature_places.CNUC_DATASET_URL,
        edition=nature_places.CNUC_EDITION,
        shape_tolerance=0.0005,
        batch_size=10,
    ),
    LayerSource(
        "cave",
        "nature",
        "icmbio",
        "canie/cavernas",
        "ICMBio/CECAV — Cadastro Nacional de Informações Espeleológicas (CANIE)",
        nature_places.CAVES_DATASET_URL,
        edition=nature_places.CAVES_EDITION,
    ),
    LayerSource(
        "mining_title",
        "resources",
        "anm",
        "sigmine/lavras",
        "ANM — SIGMINE, processos minerários em fase de lavra",
        resource_places.ANM_DATASET_URL,
    ),
    LayerSource(
        "mineral_occurrence",
        "resources",
        "sgb",
        "geosgb/ocorrencias-minerais",
        "Serviço Geológico do Brasil (SGB/CPRM) — ocorrências minerais do GeoSGB",
        resource_places.SGB_DATASET_URL,
    ),
    LayerSource(
        "oil_field",
        "resources",
        "anp",
        "geoanp/campos-de-producao",
        "ANP — campos de produção de petróleo e gás natural",
        resource_places.ANP_DATASET_URL,
        shape_tolerance=0.0003,
        batch_size=50,
    ),
    LayerSource(
        "sport",
        "sports",
        "osm",
        "geofabrik/brasil-esportes",
        "OpenStreetMap — locais de prática esportiva (extrato Geofabrik do Brasil)",
        osm_sports.DATASET_URL,
    ),
)
SOURCE_BY_LAYER = {source.layer: source for source in SOURCES}

_INSERT = text(
    """
    INSERT INTO places (context, layer, category, extra_categories, external_id, name, summary,
                        attributes, weight, point, shape, dataset_id)
    SELECT :context, :layer, :category, CAST(:extra_categories AS varchar(32)[]), :external_id,
           :name, :summary, CAST(:attributes AS jsonb), :weight,
           COALESCE(g.pt, ST_PointOnSurface(g.shp)),
           CASE WHEN CAST(:tolerance AS float8) IS NOT NULL
                -- A simplificação às vezes devolve um anel que se cruza; sem validar de
                -- novo, a interseção com o território falha.
                THEN ST_CollectionExtract(ST_MakeValid(
                     ST_SimplifyPreserveTopology(g.shp, CAST(:tolerance AS float8))), 3) END,
           :dataset_id
    FROM (
        SELECT CASE WHEN CAST(:longitude AS float8) IS NOT NULL
                    THEN ST_SetSRID(ST_MakePoint(CAST(:longitude AS float8),
                                                 CAST(:latitude AS float8)), 4326) END AS pt,
               CASE WHEN CAST(:geometry AS text) IS NOT NULL
                    THEN ST_CollectionExtract(ST_MakeValid(ST_SetSRID(
                         ST_GeomFromGeoJSON(CAST(:geometry AS text)), 4326)), 3) END AS shp
    ) g
    WHERE NOT ST_IsEmpty(COALESCE(g.pt, g.shp))
    """
)

_ASSIGN_MUNICIPALITY = text(
    """
    UPDATE places p SET municipality_code = t.ibge_code
    FROM territories t
    JOIN territory_geometries g ON g.territory_id = t.id AND g.lod = 'detail'
    WHERE t.level = 'municipality' AND p.layer = :layer AND ST_Covers(g.geom, p.point)
    """
)

# A interseção com o polígono inteiro de um bioma custa minutos; em pedaços, segundos.
_BIOME_SHARES = (
    "DELETE FROM territory_biomes",
    "CREATE TEMP TABLE biome_parts ON COMMIT DROP AS "
    "SELECT category AS biome, ST_Subdivide(shape, 256) AS geom FROM places WHERE layer = 'biome'",
    "CREATE INDEX ON biome_parts USING gist (geom)",
    """
    INSERT INTO territory_biomes (territory_code, biome, share)
    SELECT code, biome, LEAST(share, 1) FROM (
        SELECT t.ibge_code AS code, b.biome,
               ROUND((SUM(ST_Area(ST_Intersection(g.geom, b.geom)))
                      / NULLIF(ST_Area(g.geom), 0))::numeric, 4) AS share
        FROM territories t
        JOIN territory_geometries g ON g.territory_id = t.id AND g.lod = 'overview'
        JOIN biome_parts b ON ST_Intersects(g.geom, b.geom)
        WHERE t.level IN ('municipality', 'state')
        GROUP BY t.ibge_code, b.biome, g.geom
    ) shares
    WHERE share >= 0.01
    """,
)


def _row(record: PlaceRecord, source: LayerSource, dataset_id: int) -> dict[str, Any]:
    return {
        "context": source.context,
        "layer": record.layer,
        "category": record.category,
        "extra_categories": list(record.extra_categories) or None,
        "external_id": record.external_id[:64] if record.external_id else None,
        "name": record.name[:200] if record.name else None,
        "summary": record.summary[:200] if record.summary else None,
        "attributes": orjson.dumps(
            [{"label": label, "value": value} for label, value in record.attributes]
        ).decode(),
        "weight": record.weight,
        "longitude": record.longitude,
        "latitude": record.latitude,
        "geometry": orjson.dumps(record.geometry).decode() if record.geometry else None,
        "tolerance": source.shape_tolerance,
        "dataset_id": dataset_id,
    }


async def _normalized(
    features: AsyncIterator[dict[str, Any]],
    normalize: Callable[[dict[str, Any]], PlaceRecord | None],
) -> AsyncIterator[PlaceRecord]:
    async for feature in features:
        record = normalize(feature)
        if record is not None:
            yield record


async def _sports(path: Path) -> AsyncIterator[PlaceRecord]:
    with path.open("rb") as handle:
        for record in osm_sports.read(handle):
            yield record


def _records(layer: str, client: Any, osm_file: Path | None) -> AsyncIterator[PlaceRecord]:
    inde = nature_places.INDE_URL
    if layer == "biome":
        features = ogc.wfs_features(
            client,
            nature_places.BIOMES_URL,
            nature_places.BIOMES_LAYER,
            source="INPE",
            sort_by="id",
            page_size=10,
        )
        return _normalized(features, nature_places.biome)
    if layer == "conservation_unit":
        features = ogc.wfs_features(
            client,
            inde.format(workspace="MMA"),
            nature_places.CNUC_LAYER,
            source="MMA",
            sort_by="ogc_fid",
            page_size=40,
        )
        return _normalized(features, nature_places.conservation_unit)
    if layer == "cave":
        features = ogc.wfs_features(
            client,
            inde.format(workspace="ICMBio"),
            nature_places.CAVES_LAYER,
            source="ICMBio",
            sort_by="ogc_fid",
            page_size=5000,
        )
        return _normalized(features, nature_places.cave)
    if layer == "mining_title":
        features = ogc.arcgis_features(
            client,
            resource_places.ANM_URL,
            source="ANM",
            out_fields=resource_places.ANM_FIELDS,
            where=resource_places.ANM_WHERE,
            page_size=2000,
            # O mapa usa só o ponto do título: o contorno vem generalizado (~200 m).
            extra={"maxAllowableOffset": 0.002, "geometryPrecision": 5},
        )
        return _normalized(features, resource_places.mining_title)
    if layer == "mineral_occurrence":
        features = ogc.arcgis_features(
            client, resource_places.SGB_URL, source="SGB", out_fields=resource_places.SGB_FIELDS
        )
        return _normalized(features, resource_places.mineral_occurrence)
    if layer == "oil_field":
        features = ogc.wfs_features(
            client,
            resource_places.ANP_URL,
            resource_places.ANP_LAYER,
            source="ANP",
            sort_by="COD_CAMPO",
            page_size=200,
        )
        return _normalized(features, resource_places.oil_field)
    if osm_file is None:
        raise SystemExit(
            "Esportes: informe --osm-file com o arquivo gerado por scripts/sports_places/build.sh."
        )
    return _sports(osm_file)


def _edition(source: LayerSource, osm_file: Path | None) -> str | None:
    if source.edition:
        return source.edition
    if source.layer == "sport" and osm_file is not None:
        stamp = osm_file.with_suffix(".date")
        text_date = stamp.read_text().strip()[:10] if stamp.is_file() else ""
        day = (
            date.fromisoformat(text_date)
            if text_date
            else datetime.fromtimestamp(osm_file.stat().st_mtime, UTC).date()
        )
        return f"{day:%d/%m/%Y}"
    if source.layer == "biome":
        return None
    return f"consulta em {datetime.now(UTC):%d/%m/%Y}"


async def _after_load(session: AsyncSession, layer: str) -> None:
    await session.execute(_ASSIGN_MUNICIPALITY, {"layer": layer})
    if layer == "biome":
        for statement in _BIOME_SHARES:
            await session.execute(text(statement))


async def ingest_layer(source: LayerSource, osm_file: Path | None) -> int:
    async with (
        http_client(timeout=600, verify=resource_places.tls_context()) as client,
        job_session(job=JOB_NAME, source=source.source, dataset_code=source.code) as (
            session,
            _run_id,
            report,
        ),
    ):
        dataset_id = await upsert_dataset(
            session, source=source.source, code=source.code, name=source.name, url=source.url
        )
        await session.execute(
            text("DELETE FROM places WHERE layer = :layer"), {"layer": source.layer}
        )
        batch: list[dict[str, Any]] = []
        async for record in _records(source.layer, client, osm_file):
            report.processed += 1
            batch.append(_row(record, source, dataset_id))
            if len(batch) >= source.batch_size:
                await session.execute(_INSERT, batch)
                batch = []
        if batch:
            await session.execute(_INSERT, batch)
        if report.processed == 0:
            raise RuntimeError(f"A fonte de {source.layer} não devolveu nenhuma feição.")
        await _after_load(session, source.layer)
        written = await session.execute(
            text("SELECT COUNT(*) FROM places WHERE layer = :layer"), {"layer": source.layer}
        )
        report.written = int(written.scalar_one())
        report.details = {
            "layer": source.layer,
            "count": report.written,
            "edition": _edition(source, osm_file),
        }
        await session.commit()
    print(f"{source.layer}: {report.written} lugares.")
    return report.written


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Importa lugares de Natureza (INPE, MMA, ICMBio), Recursos (ANM, SGB, ANP) "
        "e Esportes (OpenStreetMap)."
    )
    parser.add_argument(
        "--context", choices=("nature", "resources", "sports", "all"), default="all"
    )
    parser.add_argument("--only", help="Camadas separadas por vírgula, no lugar do contexto.")
    parser.add_argument(
        "--osm-file",
        type=Path,
        help="GeoJSON por linha gerado por scripts/sports_places/build.sh (camada sport).",
    )
    args = parser.parse_args()
    if args.osm_file is not None and not args.osm_file.is_file():
        parser.error(f"Arquivo não encontrado: {args.osm_file}")
    if args.only:
        unknown = [layer for layer in args.only.split(",") if layer not in SOURCE_BY_LAYER]
        if unknown:
            parser.error(f"Camada desconhecida: {', '.join(unknown)}")
        chosen = [SOURCE_BY_LAYER[layer] for layer in args.only.split(",")]
    else:
        chosen = [s for s in SOURCES if args.context in ("all", s.context)]
        if args.context == "all" and args.osm_file is None:
            chosen = [s for s in chosen if s.layer != "sport"]
    failed = 0
    for source in chosen:
        try:
            await ingest_layer(source, args.osm_file)
        except Exception as exc:  # uma fonte fora do ar não impede as outras
            failed += 1
            print(f"{source.layer}: falhou ({type(exc).__name__}: {exc}).")
    return 1 if failed else 0


if __name__ == "__main__":
    run_job(main)
