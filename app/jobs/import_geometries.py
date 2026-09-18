"""Importa as malhas territoriais oficiais e gera os LODs de visualização.

`python -m app.jobs.import_geometries [--skip-municipalities] [--states 35,31]`

É aqui que mora a decisão de performance mais importante do produto: **toda a
simplificação geométrica acontece nesta ingestão**, nunca em tempo de
requisição. A malha municipal de Minas Gerais tem 8,8 MB e 401.746 vértices na
qualidade máxima; nenhum endpoint pode pagar esse custo por requisição.

Pipeline por território:

1. baixar a malha canônica (`qualidade=maxima`);
2. normalizar para `MultiPolygon` válido e gravar como LOD `canonical`;
3. derivar `overview` e `detail` com `ST_SimplifyPreserveTopology`;
4. gravar o bounding box em `territories`, para o drill-down do mapa não
   precisar ler geometria.

**Idempotência** vem da PK `(territory_id, lod)`: reexecutar sobrescreve a
geometria do mesmo território/LOD.

Limitação conhecida e aceita: a simplificação é feita por feature, então
fronteiras compartilhadas entre vizinhos podem divergir em frações de pixel. Nas
tolerâncias usadas (2 km na visão do país, 200 m na visão de estado) o desvio
fica abaixo de 1 pixel nos zooms correspondentes. Se for preciso simplificar
mais agressivamente, o próximo passo é simplificação topológica entre features
(mapshaper/PostGIS Topology) ou vector tiles — não é necessário no MVP.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import orjson
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.db.results import affected_rows
from app.jobs._runner import RunReport, job_session, run_job, upsert_dataset
from app.models import GeometryLOD, Territory, TerritoryLevel
from app.providers.base import http_client
from app.providers.ibge import malhas
from app.providers.ibge.records import GeometryRecord

logger = get_logger(__name__)

JOB_NAME = "import_geometries"

# LODs derivados e suas tolerâncias (em graus, SRID 4326).
_DERIVED_LODS: dict[GeometryLOD, float] = {
    GeometryLOD.OVERVIEW: settings.geometry_overview_tolerance,
    GeometryLOD.DETAIL: settings.geometry_detail_tolerance,
}

# ST_MakeValid + ST_Multi + ST_CollectionExtract(..,3) garantem MultiPolygon
# válido: a malha do IBGE mistura Polygon e MultiPolygon na mesma resposta.
_UPSERT_CANONICAL_SQL = text(
    """
    WITH source AS (
        SELECT t.id AS territory_id,
               ST_CollectionExtract(
                   ST_Multi(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(:geojson), 4326))),
                   3
               ) AS geom
          FROM territories t
         WHERE t.ibge_code = :ibge_code
    )
    INSERT INTO territory_geometries (
        territory_id, lod, geom, simplify_tolerance, vertex_count,
        dataset_id, created_at, updated_at
    )
    SELECT territory_id, 'canonical', geom, NULL, ST_NPoints(geom),
           :dataset_id, now(), now()
      FROM source
     WHERE NOT ST_IsEmpty(geom)
    ON CONFLICT (territory_id, lod) DO UPDATE
       SET geom         = EXCLUDED.geom,
           vertex_count = EXCLUDED.vertex_count,
           dataset_id   = EXCLUDED.dataset_id,
           updated_at   = now()
    """
)

# Simplificação em conjunto (set-based), por nível territorial.
# O CASE é uma proteção real: simplificar uma ilha muito pequena pode produzir
# geometria vazia, e um município que desaparece do mapa é um bug visível.
# Nesse caso mantemos a geometria canônica.
_DERIVE_LOD_SQL = text(
    """
    WITH simplified AS (
        SELECT g.territory_id,
               g.geom AS canonical_geom,
               ST_CollectionExtract(
                   ST_Multi(ST_MakeValid(
                       ST_SimplifyPreserveTopology(g.geom, :tolerance)
                   )),
                   3
               ) AS simplified_geom
          FROM territory_geometries g
          JOIN territories t ON t.id = g.territory_id
         WHERE g.lod = 'canonical'
           AND t.level = CAST(:level AS territory_level)
    ),
    resolved AS (
        SELECT territory_id,
               CASE
                   WHEN simplified_geom IS NULL OR ST_IsEmpty(simplified_geom)
                       THEN canonical_geom
                   ELSE simplified_geom
               END AS geom
          FROM simplified
    )
    INSERT INTO territory_geometries (
        territory_id, lod, geom, simplify_tolerance, vertex_count,
        dataset_id, created_at, updated_at
    )
    SELECT territory_id, CAST(:lod AS geometry_lod), geom, :tolerance,
           ST_NPoints(geom), :dataset_id, now(), now()
      FROM resolved
    ON CONFLICT (territory_id, lod) DO UPDATE
       SET geom               = EXCLUDED.geom,
           simplify_tolerance = EXCLUDED.simplify_tolerance,
           vertex_count       = EXCLUDED.vertex_count,
           dataset_id         = EXCLUDED.dataset_id,
           updated_at         = now()
    """
)

# bbox gravado a partir da geometria canônica (extensão verdadeira).
_UPDATE_BBOX_SQL = text(
    """
    UPDATE territories t
       SET bbox_west  = b.west,
           bbox_south = b.south,
           bbox_east  = b.east,
           bbox_north = b.north,
           updated_at = now()
      FROM (
          SELECT g.territory_id,
                 ST_XMin(g.geom) AS west,
                 ST_YMin(g.geom) AS south,
                 ST_XMax(g.geom) AS east,
                 ST_YMax(g.geom) AS north
            FROM territory_geometries g
           WHERE g.lod = 'canonical'
      ) AS b
     WHERE t.id = b.territory_id
       AND (t.bbox_west  IS DISTINCT FROM b.west
         OR t.bbox_south IS DISTINCT FROM b.south
         OR t.bbox_east  IS DISTINCT FROM b.east
         OR t.bbox_north IS DISTINCT FROM b.north)
    """
)

# Territórios sem geometria canônica. Isso acontece de verdade: municípios
# recém-criados aparecem na API Localidades antes de entrarem na malha
# territorial. O mapa simplesmente não os desenha (INNER JOIN na geometria), e
# esconder o fato tornaria a lacuna invisível — por isso ela é reportada.
_MISSING_GEOMETRY_SQL = text(
    """
    SELECT t.ibge_code, t.name, t.level::text AS level
      FROM territories t
      LEFT JOIN territory_geometries g
             ON g.territory_id = t.id AND g.lod = 'canonical'
     WHERE g.territory_id IS NULL
     ORDER BY t.level, t.ibge_code
    """
)

_VERTEX_REPORT_SQL = text(
    """
    SELECT t.level::text AS level,
           g.lod::text   AS lod,
           COUNT(*)      AS features,
           SUM(g.vertex_count) AS vertices
      FROM territory_geometries g
      JOIN territories t ON t.id = g.territory_id
     GROUP BY t.level, g.lod
     ORDER BY t.level, g.lod
    """
)


@dataclass(frozen=True, slots=True)
class _Options:
    skip_municipalities: bool
    states: tuple[str, ...] | None
    quality: malhas.Quality


async def _store_canonical(
    session: AsyncSession,
    records: list[GeometryRecord],
    *,
    dataset_id: int,
    report: RunReport,
) -> int:
    written = 0
    for record in records:
        report.processed += 1
        result = await session.execute(
            _UPSERT_CANONICAL_SQL,
            {
                "geojson": orjson.dumps(record.geojson).decode(),
                "ibge_code": record.ibge_code,
                "dataset_id": dataset_id,
            },
        )
        if affected_rows(result):
            written += 1
        else:
            # Geometria sem território correspondente: indica que
            # import_territories não rodou ou está desatualizado.
            report.record_failure(f"geometry:{record.ibge_code}", "território inexistente no banco")
            report.failed += 1
    await session.commit()
    return written


async def _derive_lods(session: AsyncSession, level: TerritoryLevel, dataset_id: int) -> None:
    for lod, tolerance in _DERIVED_LODS.items():
        await session.execute(
            _DERIVE_LOD_SQL,
            {
                "tolerance": tolerance,
                "lod": lod.value,
                "level": level.value,
                "dataset_id": dataset_id,
            },
        )
    await session.commit()
    logger.info("geometries.lods_derived", extra={"level": level.value})


async def _state_codes(session: AsyncSession, requested: tuple[str, ...] | None) -> list[str]:
    statement = select(Territory.ibge_code).where(Territory.level == TerritoryLevel.STATE)
    if requested:
        statement = statement.where(Territory.ibge_code.in_(requested))
    codes = list((await session.execute(statement.order_by(Territory.ibge_code))).scalars())
    if requested:
        missing = set(requested) - set(codes)
        if missing:
            raise RuntimeError(f"UFs inexistentes: {sorted(missing)}")
    return codes


async def _import_municipalities(
    session: AsyncSession,
    client: httpx.AsyncClient,
    *,
    state_codes: list[str],
    dataset_id: int,
    quality: malhas.Quality,
    report: RunReport,
) -> int:
    """Baixa a malha municipal UF por UF, com concorrência limitada.

    A concorrência é limitada de propósito: cada resposta chega a ~9 MB, então
    baixar tudo de uma vez custaria centenas de MB de memória sem ganho real.
    """
    written = 0
    batch_size = max(1, settings.ibge_max_concurrency)

    for start in range(0, len(state_codes), batch_size):
        batch = state_codes[start : start + batch_size]
        results = await asyncio.gather(
            *(
                malhas.fetch_municipalities_of_state(client, code, quality=quality)
                for code in batch
            ),
            return_exceptions=True,
        )
        for code, result in zip(batch, results, strict=True):
            if isinstance(result, BaseException):
                # Uma UF que falha não invalida as demais: o run termina
                # como 'partial' e o escopo falho fica registrado.
                report.record_failure(f"state:{code}", str(result))
                report.failed += 1
                continue
            stored = await _store_canonical(session, result, dataset_id=dataset_id, report=report)
            written += stored
            logger.info(
                "geometries.state_imported",
                extra={"state": code, "municipalities": stored},
            )
    return written


async def _report_vertices(session: AsyncSession, report: RunReport) -> None:
    rows = (await session.execute(_VERTEX_REPORT_SQL)).all()
    report.details["geometry_summary"] = [
        {
            "level": row.level,
            "lod": row.lod,
            "features": row.features,
            "vertices": int(row.vertices or 0),
        }
        for row in rows
    ]

    missing = (await session.execute(_MISSING_GEOMETRY_SQL)).all()
    report.details["missing_geometry"] = [
        {"ibgeCode": row.ibge_code, "name": row.name, "level": row.level} for row in missing
    ]
    if missing:
        logger.warning(
            "geometries.missing",
            extra={
                "count": len(missing),
                "sample": [row.ibge_code for row in missing[:5]],
            },
        )


async def main() -> int:
    options = _parse_args()

    async with job_session(
        job=JOB_NAME,
        source=malhas.SOURCE,
        dataset_code=malhas.DATASET_CODE,
    ) as (session, _run_id, report):
        dataset_id = await upsert_dataset(
            session,
            source=malhas.SOURCE,
            code=malhas.DATASET_CODE,
            name=malhas.DATASET_NAME,
            url=malhas.DATASET_URL,
            source_updated_at=datetime.now(UTC),
        )
        await session.commit()

        async with http_client() as client:
            # País, regiões e UFs: três requisições pequenas.
            for level in (TerritoryLevel.COUNTRY, TerritoryLevel.REGION, TerritoryLevel.STATE):
                try:
                    records = await _fetch_level(client, level, options.quality)
                except ProviderError as exc:
                    report.record_failure(f"level:{level.value}", str(exc))
                    report.failed += 1
                    continue
                report.written += await _store_canonical(
                    session, records, dataset_id=dataset_id, report=report
                )
                await _derive_lods(session, level, dataset_id)

            if not options.skip_municipalities:
                codes = await _state_codes(session, options.states)
                report.written += await _import_municipalities(
                    session,
                    client,
                    state_codes=codes,
                    dataset_id=dataset_id,
                    quality=options.quality,
                    report=report,
                )
                await _derive_lods(session, TerritoryLevel.MUNICIPALITY, dataset_id)

        await session.execute(_UPDATE_BBOX_SQL)
        await session.commit()
        await _report_vertices(session, report)

    print("Geometrias importadas:")
    for entry in report.details.get("geometry_summary", []):
        print(
            f"  {entry['level']:<14} {entry['lod']:<10} "
            f"{entry['features']:>5} features  {entry['vertices']:>9} vértices"
        )
    missing = report.details.get("missing_geometry", [])
    if missing:
        print(f"\nTerritórios sem geometria na malha do IBGE ({len(missing)}):")
        for entry in missing[:10]:
            print(f"  {entry['ibgeCode']:<9} {entry['name']} ({entry['level']})")
        print("  Causa usual: município criado após a última publicação da malha.")
    if report.failures:
        print(f"  ATENÇÃO: {len(report.failures)} escopo(s) falharam (ver ingestion_runs.details).")
    return 0


async def _fetch_level(
    client: httpx.AsyncClient,
    level: TerritoryLevel,
    quality: malhas.Quality,
) -> list[GeometryRecord]:
    """Busca a malha do nível pedido. O país devolve uma feature; os outros, várias."""
    if level is TerritoryLevel.COUNTRY:
        return [await malhas.fetch_country(client, quality)]
    if level is TerritoryLevel.REGION:
        return await malhas.fetch_regions(client, quality)
    if level is TerritoryLevel.STATE:
        return await malhas.fetch_states(client, quality)
    raise ValueError(f"Nível sem malha agregada: {level}")


def _parse_args() -> _Options:
    parser = argparse.ArgumentParser(description="Importa malhas territoriais do IBGE.")
    parser.add_argument(
        "--skip-municipalities",
        action="store_true",
        help="Importa apenas país, regiões e UFs (ingestão rápida para desenvolvimento).",
    )
    parser.add_argument(
        "--states",
        help="Limita a importação municipal às UFs informadas (ex.: 35,31).",
    )
    parser.add_argument(
        "--quality",
        choices=("minima", "intermediaria", "maxima"),
        default="maxima",
        help="Qualidade da malha de origem. 'maxima' é a canônica.",
    )
    args = parser.parse_args()
    return _Options(
        skip_municipalities=args.skip_municipalities,
        states=tuple(code.strip() for code in args.states.split(",")) if args.states else None,
        quality=args.quality,
    )


if __name__ == "__main__":
    run_job(main)
