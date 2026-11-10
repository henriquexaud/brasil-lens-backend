"""Lugares dos contextos Natureza, Recursos e Esportes (ADR-25).

O mapa mostra pouco de cada vez: de longe, a contagem por território; de perto,
os pontos da vista. Áreas (biomas, unidades de conservação, campos de petróleo)
chegam simplificadas para a escala. O detalhe de um lugar vem do clique.
"""

from __future__ import annotations

from dataclasses import dataclass

import orjson
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTLCache
from app.core.errors import InvalidParameterError, NotFoundError, TerritoryNotFoundError
from app.repositories import places as repo
from app.schemas.common import to_json
from app.schemas.places import (
    BiomeShare,
    PlaceArea,
    PlaceAreaProperties,
    PlaceAreas,
    PlaceAttribute,
    PlaceCatalog,
    PlaceCategory,
    PlaceContext,
    PlaceDetail,
    PlaceItem,
    PlaceLayer,
    PlaceLayerInfo,
    PlaceLevel,
    PlaceMunicipality,
    PlacePoint,
    PlacePoints,
    PlaceSource,
    PlaceSummary,
    PlaceValues,
)


@dataclass(frozen=True, slots=True)
class Layer:
    key: PlaceLayer
    context: PlaceContext
    label: str
    source: str
    source_label: str
    dataset: str
    has_shapes: bool = False
    # Zoom a partir do qual os pontos da vista são servidos.
    point_zoom: float | None = None


LAYERS: tuple[Layer, ...] = (
    Layer("biome", "nature", "Biomas", "inpe", "IBGE · INPE", "terrabrasilis/biomas", True),
    Layer(
        "conservation_unit",
        "nature",
        "Unidades de conservação",
        "mma",
        "MMA",
        "cnuc/unidades-de-conservacao",
        True,
    ),
    Layer("cave", "nature", "Cavernas", "icmbio", "ICMBio", "canie/cavernas", point_zoom=8),
    Layer("mining_title", "resources", "Lavras", "anm", "ANM", "sigmine/lavras", point_zoom=8),
    Layer(
        "mineral_occurrence",
        "resources",
        "Ocorrências minerais",
        "sgb",
        "SGB",
        "geosgb/ocorrencias-minerais",
        point_zoom=7,
    ),
    Layer(
        "oil_field",
        "resources",
        "Campos de petróleo e gás",
        "anp",
        "ANP",
        "geoanp/campos-de-producao",
        True,
    ),
    Layer(
        "sport",
        "sports",
        "Locais de esporte",
        "osm",
        "OpenStreetMap",
        "geofabrik/brasil-esportes",
        point_zoom=10,
    ),
)
LAYER = {layer.key: layer for layer in LAYERS}

_MINERALS = {
    "rare_earths": "Terras raras",
    "lithium": "Lítio",
    "niobium": "Nióbio e tântalo",
    "gold": "Ouro",
    "iron": "Ferro",
    "copper": "Cobre",
    "aluminum": "Alumínio",
    "manganese": "Manganês",
    "nickel": "Níquel",
    "tin": "Estanho",
    "gems": "Gemas e diamante",
    "energy": "Carvão, urânio e outros energéticos",
    "fertilizers": "Fosfato e potássio",
    "limestone": "Calcário e gesso",
    "stone": "Rochas ornamentais",
    "construction": "Areia, brita e argila",
    "water": "Água mineral",
    "other": "Outros",
}
# A interface lista as categorias da mais numerosa para a menos; a ordem daqui só desempata.
CATEGORIES: dict[str, dict[str, str]] = {
    "biome": {
        "amazon": "Amazônia",
        "cerrado": "Cerrado",
        "atlantic_forest": "Mata Atlântica",
        "caatinga": "Caatinga",
        "pampa": "Pampa",
        "pantanal": "Pantanal",
    },
    "conservation_unit": {
        "full_protection": "Proteção integral",
        "sustainable_use": "Uso sustentável",
    },
    "cave": {},
    "mining_title": _MINERALS,
    "mineral_occurrence": _MINERALS,
    "oil_field": {"oil": "Óleo", "gas": "Gás natural"},
    "sport": {
        "soccer": "Futebol",
        "basketball": "Basquete",
        "volleyball": "Vôlei",
        "multi": "Poliesportiva",
        "tennis": "Tênis",
        "skate": "Skate",
        "swimming": "Natação",
        "fitness": "Academia ao ar livre",
        "athletics": "Atletismo",
        "centre": "Centro esportivo",
        "stadium": "Estádio",
    },
}


@dataclass(frozen=True, slots=True)
class Detail:
    name: str
    min_zoom: float
    tolerance: float
    digits: int
    max_span: float
    # Área menor que isto (graus) não chega a um pixel nessa escala.
    min_size: float


# As mesmas faixas do traçado do Transporte (o frontend encaixa a vista na mesma grade),
# com folga na extensão: aqui a área não é limitada à UF aberta.
DETAILS = (
    Detail("overview", 0, 0.02, 2, 360, 0.12),
    Detail("regional", 6, 0.005, 3, 60, 0.03),
    Detail("local", 8, 0.001, 4, 16, 0.006),
    Detail("street", 10, 0.0002, 5, 6, 0),
)
MAX_POINTS = 2500
MAX_POINT_SPAN = 40.0
MAX_ITEMS = 60
CACHE_SECONDS = 24 * 60 * 60
_cache: TTLCache[bytes] = TTLCache(ttl_seconds=CACHE_SECONDS, max_entries=512)
_version_cache: TTLCache[int] = TTLCache(ttl_seconds=60, max_entries=1)


def clear_cache() -> None:
    _cache.clear()
    _version_cache.clear()


def detail_for(zoom: float) -> Detail:
    return next(detail for detail in reversed(DETAILS) if zoom >= detail.min_zoom)


async def _version(session: AsyncSession) -> int:
    cached = _version_cache.get("version")
    if cached is None:
        cached = await repo.fetch_version(session)
        _version_cache.set("version", cached)
    return cached


def _category(layer: PlaceLayer, category: str | None) -> str | None:
    if category is not None and category not in CATEGORIES[layer]:
        raise InvalidParameterError("Categoria desconhecida para esta camada.", "category")
    return category


def _label(layer: str, category: str | None) -> str | None:
    return CATEGORIES[layer].get(category) if category else None


def _categories(layer: str, counts: dict[str | None, int]) -> list[PlaceCategory]:
    found = [
        PlaceCategory(key=key, label=label, count=counts[key])
        for key, label in CATEGORIES[layer].items()
        if counts.get(key)
    ]
    # "Outros" fecha a lista, por maior que seja.
    return sorted(found, key=lambda item: (item.key == "other", -item.count))


def _source(layer: Layer, name: str, url: str | None, edition: str | None) -> PlaceSource:
    return PlaceSource(
        key=layer.source, label=layer.source_label, edition=edition, description=name, url=url
    )


async def get_catalog(session: AsyncSession, context: PlaceContext) -> bytes:
    version = await _version(session)
    key = ("catalog", context, version)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    layers = [layer for layer in LAYERS if layer.context == context]
    counts: dict[str, dict[str | None, int]] = {}
    for row in await repo.fetch_category_counts(session, context):
        counts.setdefault(row.layer, {})[row.category] = int(row.total)
    totals = await repo.fetch_layer_counts(session, context)
    runs = await repo.fetch_layer_runs(session)
    datasets = await repo.fetch_datasets(session, [layer.dataset for layer in layers])
    body = to_json(
        PlaceCatalog(
            context=context,
            version=version,
            layers=[
                PlaceLayerInfo(
                    key=layer.key,
                    label=layer.label,
                    source=layer.source,
                    has_shapes=layer.has_shapes,
                    point_zoom=layer.point_zoom,
                    count=totals.get(layer.key, 0),
                    categories=_categories(layer.key, counts.get(layer.key, {})),
                )
                for layer in layers
            ],
            sources=[
                _source(
                    layer,
                    datasets[layer.dataset].name,
                    datasets[layer.dataset].url,
                    runs.get(layer.key, {}).get("edition"),
                )
                for layer in layers
                if layer.dataset in datasets
            ],
        )
    )
    _cache.set(key, body)
    return body


async def get_values(
    session: AsyncSession,
    *,
    layer: PlaceLayer,
    category: str | None,
    level: PlaceLevel,
    parent: str | None,
) -> bytes:
    if LAYER[layer].has_shapes:
        raise InvalidParameterError("Esta camada é de áreas e não tem contagem.", "layer")
    category = _category(layer, category)
    if parent is not None and not (len(parent) == 2 and parent.isdigit()):
        raise InvalidParameterError("O recorte deve ser o código de uma UF.", "parent")
    if level == "state" and parent is not None:
        raise InvalidParameterError("O nível estadual não tem recorte.", "parent")
    version = await _version(session)
    key = ("values", layer, category, level, parent, version)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    values = await repo.fetch_values(session, layer, category, 2 if level == "state" else 7, parent)
    body = to_json(
        PlaceValues(
            layer=layer,
            category=category,
            level=level,
            parent=parent,
            total=sum(values.values()),
            values=values,
        )
    )
    _cache.set(key, body)
    return body


async def get_points(
    session: AsyncSession, *, layer: PlaceLayer, category: str | None, bbox: repo.BBox
) -> bytes:
    if LAYER[layer].point_zoom is None:
        raise InvalidParameterError("Esta camada é de áreas e não tem pontos.", "layer")
    category = _category(layer, category)
    if bbox[2] - bbox[0] > MAX_POINT_SPAN or bbox[3] - bbox[1] > MAX_POINT_SPAN:
        raise InvalidParameterError("Aproxime o mapa para ver os pontos.", "bbox")
    version = await _version(session)
    key = ("points", layer, category, bbox, version)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    rows = await repo.fetch_points(session, layer, category, bbox, MAX_POINTS + 1)
    body = to_json(
        PlacePoints(
            layer=layer,
            truncated=len(rows) > MAX_POINTS,
            points=[
                PlacePoint(
                    id=row.id,
                    name=row.name,
                    category=row.category,
                    longitude=round(row.longitude, 5),
                    latitude=round(row.latitude, 5),
                )
                for row in rows[:MAX_POINTS]
            ],
        )
    )
    _cache.set(key, body)
    return body


async def get_areas(
    session: AsyncSession,
    *,
    layer: PlaceLayer,
    category: str | None,
    zoom: float,
    bbox: repo.BBox | None,
) -> bytes:
    if not LAYER[layer].has_shapes:
        raise InvalidParameterError("Esta camada é de pontos e não tem áreas.", "layer")
    category = _category(layer, category)
    detail = detail_for(zoom)
    if detail.name == "overview":
        bbox = None  # o desenho nacional não depende da vista
    elif bbox is None:
        raise InvalidParameterError("Informe a área a partir do zoom 6.", "bbox")
    elif bbox[2] - bbox[0] > detail.max_span or bbox[3] - bbox[1] > detail.max_span:
        raise InvalidParameterError("Área grande demais para esta escala.", "bbox")
    version = await _version(session)
    key = ("areas", layer, category, detail.name, bbox, version)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    # Campos de petróleo são pequenos e poucos: todos entram, mesmo de longe.
    min_size = 0 if layer == "oil_field" else detail.min_size
    rows = await repo.fetch_areas(
        session, layer, category, bbox, detail.tolerance, detail.digits, min_size
    )
    body = to_json(
        PlaceAreas(
            layer=layer,
            detail=detail.name,
            features=[
                PlaceArea(
                    id=row.id,
                    properties=PlaceAreaProperties(name=row.name, category=row.category),
                    geometry=orjson.loads(row.geometry),
                )
                for row in rows
            ],
        )
    )
    _cache.set(key, body)
    return body


async def get_place(session: AsyncSession, place_id: int) -> PlaceDetail:
    row = await repo.fetch_place(session, place_id)
    if row is None:
        raise NotFoundError("Lugar não encontrado.")
    layer = LAYER[row.layer]
    dataset = (await repo.fetch_datasets(session, [layer.dataset])).get(layer.dataset)
    edition = (await repo.fetch_layer_runs(session)).get(layer.key, {}).get("edition")
    return PlaceDetail(
        id=row.id,
        layer=layer.key,
        layer_label=layer.label,
        category=row.category,
        category_label=_label(layer.key, row.category),
        name=row.name,
        summary=row.summary,
        municipality=PlaceMunicipality(
            code=row.municipality_code, name=row.municipality_name, state=row.state
        )
        if row.municipality_code and row.municipality_name
        else None,
        longitude=round(row.longitude, 5),
        latitude=round(row.latitude, 5),
        attributes=[PlaceAttribute(**item) for item in row.attributes or []],
        source=_source(layer, dataset.name, dataset.url, edition) if dataset else None,
    )


async def get_summary(
    session: AsyncSession, *, code: str, layer: PlaceLayer, category: str | None
) -> bytes:
    category = _category(layer, category)
    version = await _version(session)
    key = ("summary", code, layer, category, version)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    territory = await repo.fetch_territory(session, code)
    if territory is None:
        raise TerritoryNotFoundError(code)
    info = LAYER[layer]
    country = territory.level == "country"
    if info.has_shapes:
        counts, total, rows = await repo.fetch_territory_areas(
            session, layer, category, None if country else territory.id, MAX_ITEMS
        )
    else:
        counts, total, rows = await repo.fetch_territory_points(
            session, layer, category, "" if country else territory.ibge_code, MAX_ITEMS
        )
    imported = layer in await repo.fetch_layer_runs(session)
    biomes = (
        await repo.fetch_biomes(session, territory.ibge_code) if info.context == "nature" else []
    )
    body = to_json(
        PlaceSummary(
            code=territory.ibge_code,
            name=territory.name,
            layer=layer,
            category=category,
            total=total if imported else None,
            categories=_categories(layer, counts),
            items=[
                PlaceItem(
                    id=row.id,
                    name=row.name,
                    category=row.category,
                    category_label=_label(layer, row.category),
                    summary=row.summary,
                    longitude=round(row.longitude, 5),
                    latitude=round(row.latitude, 5),
                )
                for row in rows
            ],
            biomes=[
                BiomeShare(
                    key=row.biome,
                    label=CATEGORIES["biome"].get(row.biome, row.biome),
                    share=float(row.share),
                )
                for row in biomes
            ],
        )
    )
    _cache.set(key, body)
    return body
