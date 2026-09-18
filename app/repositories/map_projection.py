"""Projeção de leitura do mapa: uma query, uma resposta.

Esta é a consulta mais importante do produto. Ela resolve, de uma vez:

* o escopo territorial (todos os estados, ou os municípios de uma UF);
* o ano efetivo quando o cliente pede ``latest``;
* o valor do indicador por território (inclusive ausência de valor);
* a geometria **já simplificada** no LOD pedido;
* o bounding box do escopo, para o mapa dar `fitBounds` sem ler geometria.

Decisões deliberadas:

* ``LEFT JOIN`` no valor — um território sem dado precisa ser desenhado (com
  estilo de "sem dado"), não omitido.
* ``INNER JOIN`` na geometria — sem geometria não há o que desenhar.
* Estatísticas e classificação **não** são calculadas aqui. São poucas centenas
  de números e virariam colunas repetidas em cada linha; além disso, calculá-las
  em `services/classification.py` as torna testáveis sem banco.
* Nenhuma simplificação em tempo de requisição: ``ST_AsGeoJSON`` lê geometria já
  reduzida na ingestão.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GeometryLOD, TerritoryLevel


@dataclass(slots=True)
class MapFeatureRow:
    ibge_code: str
    name: str
    level: str
    abbreviation: str | None
    parent_ibge_code: str | None
    parent_name: str | None
    value: Decimal | None
    # GeoJSON ainda como texto: quem serializa decide quando (e se) desserializa.
    geometry_json: str
    bbox: tuple[float | None, float | None, float | None, float | None]


@dataclass(slots=True)
class MapProjection:
    features: list[MapFeatureRow]
    # Ano efetivamente usado. Difere do pedido quando o cliente manda `latest`.
    resolved_year: int | None


_MAP_SQL = text(
    """
    WITH scope AS (
        SELECT t.id,
               t.ibge_code,
               t.name,
               t.level::text      AS level,
               t.abbreviation,
               t.bbox_west,
               t.bbox_south,
               t.bbox_east,
               t.bbox_north,
               p.ibge_code        AS parent_ibge_code,
               p.name             AS parent_name
          FROM territories t
          LEFT JOIN territories p ON p.id = t.parent_id
         WHERE t.level = CAST(:level AS territory_level)
           AND (CAST(:parent_id AS integer) IS NULL
                OR t.parent_id = CAST(:parent_id AS integer))
    ),
    -- Resolução de `latest` no MESMO round-trip: o último ano disponível
    -- daquele indicador DENTRO deste escopo territorial.
    target_year AS (
        SELECT CASE
                   WHEN CAST(:year AS smallint) IS NOT NULL
                       THEN CAST(:year AS smallint)
                   ELSE (
                       SELECT MAX(v.reference_year)
                         FROM indicator_values v
                        WHERE v.indicator_id = CAST(:indicator_id AS smallint)
                          AND v.territory_id IN (SELECT id FROM scope)
                   )
               END AS reference_year
    )
    SELECT s.ibge_code,
           s.name,
           s.level,
           s.abbreviation,
           s.parent_ibge_code,
           s.parent_name,
           s.bbox_west,
           s.bbox_south,
           s.bbox_east,
           s.bbox_north,
           v.value,
           (SELECT reference_year FROM target_year) AS resolved_year,
           ST_AsGeoJSON(g.geom) AS geometry_json
      FROM scope s
      JOIN territory_geometries g
             ON g.territory_id = s.id
            AND g.lod = CAST(:lod AS geometry_lod)
      LEFT JOIN indicator_values v
             ON v.territory_id = s.id
            AND v.indicator_id = CAST(:indicator_id AS smallint)
            AND v.reference_year = (SELECT reference_year FROM target_year)
     ORDER BY s.name
    """
)


async def fetch_map_projection(
    session: AsyncSession,
    *,
    level: TerritoryLevel,
    lod: GeometryLOD,
    parent_id: int | None = None,
    indicator_id: int | None = None,
    year: int | None = None,
) -> MapProjection:
    """Executa a projeção do mapa.

    `indicator_id=None` devolve apenas a geometria (mapa base sem coropleta):
    o LEFT JOIN simplesmente não casa e todos os valores vêm nulos, sem
    precisar de uma segunda query.
    """
    result = await session.execute(
        _MAP_SQL,
        {
            "level": level.value,
            "lod": lod.value,
            "parent_id": parent_id,
            "indicator_id": indicator_id,
            "year": year,
        },
    )

    features: list[MapFeatureRow] = []
    resolved_year: int | None = None
    for row in result:
        resolved_year = row.resolved_year
        features.append(
            MapFeatureRow(
                ibge_code=row.ibge_code,
                name=row.name,
                level=row.level,
                abbreviation=row.abbreviation,
                parent_ibge_code=row.parent_ibge_code,
                parent_name=row.parent_name,
                value=row.value,
                geometry_json=row.geometry_json,
                bbox=(row.bbox_west, row.bbox_south, row.bbox_east, row.bbox_north),
            )
        )

    return MapProjection(features=features, resolved_year=resolved_year)
