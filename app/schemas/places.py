from __future__ import annotations

from typing import Any, Literal

from app.schemas.common import CamelModel

PlaceContext = Literal["nature", "resources", "sports"]
PlaceLayer = Literal[
    "biome",
    "conservation_unit",
    "cave",
    "mining_title",
    "mineral_occurrence",
    "oil_field",
    "sport",
]
PlaceLevel = Literal["state", "municipality"]
SpeciesGroup = Literal[
    "birds", "mammals", "reptiles", "amphibians", "insects", "plants", "fungi", "threatened"
]


class PlaceCategory(CamelModel):
    key: str
    label: str
    count: int


class PlaceLayerInfo(CamelModel):
    key: PlaceLayer
    label: str
    source: str
    # Camada de áreas (contornos) ou de pontos; só as de pontos são contadas por território.
    has_shapes: bool
    point_zoom: float | None
    count: int
    categories: list[PlaceCategory]


class PlaceSource(CamelModel):
    key: str
    label: str
    edition: str | None = None
    description: str
    url: str | None = None


class PlaceCatalog(CamelModel):
    context: PlaceContext
    version: int
    layers: list[PlaceLayerInfo]
    sources: list[PlaceSource]


class PlaceValues(CamelModel):
    layer: PlaceLayer
    category: str | None
    level: PlaceLevel
    parent: str | None
    total: int
    # Só territórios com pelo menos um lugar; fora daqui a contagem é zero.
    values: dict[str, int]


class PlacePoint(CamelModel):
    id: int
    name: str | None
    category: str | None
    longitude: float
    latitude: float


class PlacePoints(CamelModel):
    layer: PlaceLayer
    truncated: bool
    points: list[PlacePoint]


class PlaceAreaProperties(CamelModel):
    name: str | None
    category: str | None


class PlaceArea(CamelModel):
    type: Literal["Feature"] = "Feature"
    id: int
    properties: PlaceAreaProperties
    geometry: dict[str, Any]


class PlaceAreas(CamelModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    layer: PlaceLayer
    detail: str
    features: list[PlaceArea]


class PlaceAttribute(CamelModel):
    label: str
    value: str


class PlaceMunicipality(CamelModel):
    code: str
    name: str
    state: str | None


class PlaceDetail(CamelModel):
    id: int
    layer: PlaceLayer
    layer_label: str
    category: str | None
    category_label: str | None
    name: str | None
    summary: str | None
    municipality: PlaceMunicipality | None
    longitude: float
    latitude: float
    attributes: list[PlaceAttribute]
    source: PlaceSource | None


class PlaceItem(CamelModel):
    id: int
    name: str | None
    category: str | None
    category_label: str | None
    summary: str | None
    longitude: float
    latitude: float


class BiomeShare(CamelModel):
    key: str
    label: str
    share: float


class PlaceSummary(CamelModel):
    code: str
    name: str
    layer: PlaceLayer
    category: str | None
    # `None` enquanto a camada não foi ingerida: ausência não é zero.
    total: int | None
    categories: list[PlaceCategory]
    items: list[PlaceItem]
    biomes: list[BiomeShare]


class Species(CamelModel):
    key: int
    scientific_name: str
    common_name: str | None
    family: str | None
    records: int
    # Categoria da Lista Vermelha da IUCN (CR, EN ou VU), quando a espécie é ameaçada.
    threat: Literal["CR", "EN", "VU"] | None


class SpeciesSummary(CamelModel):
    code: str
    name: str
    group: SpeciesGroup
    # Registros de ocorrência do grupo no território e espécies distintas entre eles;
    # `species_count` é `None` quando passam do que a consulta enumera.
    records: int
    species_count: int | None
    species: list[Species]
    source: PlaceSource
