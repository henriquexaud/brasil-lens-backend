"""Caso de uso do mapa: recorte, indicador, ano e classificação.

Onde ficam as regras que **não** pertencem nem ao SQL nem ao HTTP:

* política de LOD por nível territorial;
* proteção contra pedir 5.571 municípios detalhados de uma vez;
* validação de compatibilidade entre `level` e `parent` (a forma da
  hierarquia em si vive em `models/territory.py`, compartilhada com `/views`);
* tradução de `latest` para um ano concreto;
* normalização e classificação dos valores.
"""

from __future__ import annotations

import orjson
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTLCache
from app.core.config import settings
from app.core.errors import IndicatorNotFoundError, InvalidParameterError, TerritoryNotFoundError
from app.core.logging import get_logger
from app.models import EXPECTED_PARENT_LEVEL, REQUIRES_PARENT, GeometryLOD, TerritoryLevel
from app.repositories import indicators as indicators_repo
from app.repositories import map_projection as map_repo
from app.repositories import territories as territories_repo
from app.schemas.map import (
    MapClassification,
    MapFeature,
    MapFeatureCollection,
    MapFeatureProperties,
    MapIndicatorMeta,
    MapScope,
    MapStatistics,
)
from app.services.classification import DEFAULT_CLASS_COUNT, describe

logger = get_logger(__name__)

# Política de detalhe: níveis que aparecem no mapa do país inteiro usam a
# geometria mais agressiva; municípios, vistos no zoom de um estado, usam a
# intermediária. Ver docs/ARCHITECTURE.md §5.
_DEFAULT_LOD: dict[TerritoryLevel, GeometryLOD] = {
    TerritoryLevel.COUNTRY: GeometryLOD.OVERVIEW,
    TerritoryLevel.REGION: GeometryLOD.OVERVIEW,
    TerritoryLevel.STATE: GeometryLOD.OVERVIEW,
    TerritoryLevel.MUNICIPALITY: GeometryLOD.DETAIL,
}

_cache: TTLCache[MapFeatureCollection] = TTLCache(
    ttl_seconds=settings.read_cache_ttl_seconds,
    max_entries=settings.read_cache_max_entries,
)


def cache_stats() -> dict[str, int]:
    return _cache.stats()


def clear_cache() -> None:
    _cache.clear()


async def get_map(
    session: AsyncSession,
    *,
    level: TerritoryLevel,
    parent_code: str | None = None,
    indicator_key: str | None = None,
    year: str = "latest",
    lod: GeometryLOD | None = None,
    classes: int = DEFAULT_CLASS_COUNT,
) -> MapFeatureCollection:
    requested_year = (year or "latest").strip()
    target_year = _parse_year(requested_year)
    effective_lod = lod or _DEFAULT_LOD[level]

    _validate_scope(level, parent_code)

    cache_key = (level, parent_code, indicator_key, requested_year, effective_lod, classes)
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    parent_id = await _resolve_parent(session, level, parent_code)

    indicator_id: int | None = None
    catalog_entry = None
    if indicator_key:
        catalog = await indicators_repo.list_catalog(session, level=level, key=indicator_key)
        if not catalog:
            raise IndicatorNotFoundError(indicator_key)
        catalog_entry = catalog[0]
        indicator_id = await indicators_repo.get_indicator_id(session, indicator_key)

    projection = await map_repo.fetch_map_projection(
        session,
        level=level,
        lod=effective_lod,
        parent_id=parent_id,
        indicator_id=indicator_id,
        year=target_year,
    )

    distribution = describe([row.value for row in projection.features], classes=classes)

    features = [
        MapFeature(
            id=row.ibge_code,
            properties=MapFeatureProperties(
                ibge_code=row.ibge_code,
                name=row.name,
                level=TerritoryLevel(row.level),
                abbreviation=row.abbreviation,
                parent_code=row.parent_ibge_code,
                parent_name=row.parent_name,
                value=row.value,
                normalized_value=distribution.normalize(row.value),
                class_index=distribution.class_index(row.value),
            ),
            # O GeoJSON vem pronto do PostGIS; só é desserializado para virar
            # parte da resposta. Nenhuma geometria é construída em Python.
            geometry=orjson.loads(row.geometry_json),
        )
        for row in projection.features
    ]

    response = MapFeatureCollection(
        scope=MapScope(
            level=level,
            parent=parent_code,
            lod=effective_lod,
            count=len(features),
        ),
        indicator=(
            MapIndicatorMeta(
                key=catalog_entry.key,
                name=catalog_entry.name,
                unit=catalog_entry.unit,
                decimal_places=catalog_entry.decimal_places,
                year=projection.resolved_year,
                requested_year=requested_year,
                available_years=catalog_entry.available_years,
            )
            if catalog_entry is not None
            else None
        ),
        statistics=(
            MapStatistics.model_validate(distribution.statistics, from_attributes=True)
            if distribution.statistics is not None
            else None
        ),
        classification=(
            MapClassification.model_validate(distribution.classification, from_attributes=True)
            if distribution.classification is not None
            else None
        ),
        bbox=_scope_bbox(projection.features),
        features=features,
    )

    logger.info(
        "map.projection",
        extra={
            "level": level.value,
            "parent": parent_code,
            "indicator": indicator_key,
            "requested_year": requested_year,
            "resolved_year": projection.resolved_year,
            "lod": effective_lod.value,
            "features": len(features),
            "with_value": distribution.statistics.count if distribution.statistics else 0,
        },
    )

    # Cacheia apenas projeções pequenas. Uma coleção municipal desserializada
    # ocupa ~7 MB, e o acerto é baixo (cada usuário abre um estado diferente):
    # guardá-las trocaria centenas de MB de RSS por quase nenhum ganho. As
    # projeções compartilhadas por todos — a visão inicial do país — cabem.
    if len(features) <= settings.read_cache_max_features:
        _cache.set(cache_key, response)
    return response


def _parse_year(requested: str) -> int | None:
    """`latest` → None (resolvido no SQL); ano numérico → int."""
    if requested.lower() == "latest":
        return None
    try:
        parsed = int(requested)
    except ValueError as exc:
        raise InvalidParameterError(
            "O parâmetro 'year' aceita um ano (ex.: 2022) ou 'latest'.",
            parameter="year",
            received=requested,
        ) from exc
    if not 1900 <= parsed <= 2100:
        raise InvalidParameterError(
            "O parâmetro 'year' deve estar entre 1900 e 2100.",
            parameter="year",
            received=requested,
        )
    return parsed


def _validate_scope(level: TerritoryLevel, parent_code: str | None) -> None:
    if level in REQUIRES_PARENT and not parent_code:
        raise InvalidParameterError(
            f"O nível '{level.value}' exige o parâmetro 'parent' "
            "(ex.: parent=35 para os municípios de São Paulo). "
            "A restrição existe para não transferir a malha municipal inteira do país.",
            parameter="parent",
        )
    if level is TerritoryLevel.COUNTRY and parent_code:
        # Sem esta checagem o filtro por pai simplesmente não casa e a resposta
        # sai 200 com zero features — um erro de uso virando silêncio.
        raise InvalidParameterError(
            "O nível 'country' é a raiz da hierarquia e não aceita 'parent'.",
            parameter="parent",
        )


async def _resolve_parent(
    session: AsyncSession,
    level: TerritoryLevel,
    parent_code: str | None,
) -> int | None:
    if not parent_code:
        return None

    parent_level = await territories_repo.get_level_by_code(session, parent_code)
    if parent_level is None:
        raise TerritoryNotFoundError(parent_code)

    expected = EXPECTED_PARENT_LEVEL.get(level)
    if expected is not None and parent_level != expected:
        raise InvalidParameterError(
            f"Para 'level={level.value}', 'parent' deve ser um território de nível "
            f"'{expected.value}', mas '{parent_code}' é '{parent_level.value}'.",
            parameter="parent",
        )

    return await territories_repo.get_id_by_code(session, parent_code)


def _scope_bbox(
    rows: list[map_repo.MapFeatureRow],
) -> tuple[float, float, float, float] | None:
    """Extensão do escopo, agregada dos bbox gravados na ingestão.

    Permite ao cliente dar `fitBounds` sem varrer coordenadas no browser.
    """
    wests = [row.bbox[0] for row in rows if row.bbox[0] is not None]
    souths = [row.bbox[1] for row in rows if row.bbox[1] is not None]
    easts = [row.bbox[2] for row in rows if row.bbox[2] is not None]
    norths = [row.bbox[3] for row in rows if row.bbox[3] is not None]
    if not (wests and souths and easts and norths):
        return None
    return (min(wests), min(souths), max(easts), max(norths))
