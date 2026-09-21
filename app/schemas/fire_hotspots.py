"""Contratos de focos INPE: mapa completo em WMS e detalhes sob demanda."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AliasChoices, Field

from app.schemas.common import CamelModel

FireScope = Literal["country", "state", "municipality"]


class FirePoint(CamelModel):
    type: Literal["Point"] = "Point"
    coordinates: tuple[
        Annotated[float, Field(ge=-180, le=180)], Annotated[float, Field(ge=-90, le=90)]
    ]


class FireHotspotProperties(CamelModel):
    id: str
    detected_at: datetime
    satellite: str
    state: str | None = None
    municipality: str | None = None
    municipality_code: str | None = None
    biome: str | None = None
    days_without_rain: int | None = None
    precipitation_mm: float | None = None
    fire_risk: float | None = None
    frp: float | None = None


class FireHotspotFeature(CamelModel):
    type: Literal["Feature"] = "Feature"
    id: str
    properties: FireHotspotProperties
    geometry: FirePoint


class FireHotspotMetadata(CamelModel):
    level: FireScope
    parent_code: str | None = None
    hotspot_count: int
    hours: int
    source: str = "INPE · Programa Queimadas"
    source_url: str = "https://data.inpe.br/queimadas/"
    fetched_at: datetime
    window_start: datetime
    window_end: datetime
    latest_detection_at: datetime | None = None
    status: Literal["ok", "stale"] = "ok"
    wms_url: str
    wms_layer: str = "bdqueimadas:focos"
    cql_filter: str


class FireHotspotCollection(CamelModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    metadata: FireHotspotMetadata
    # A prévia contém só a detecção mais recente. O WMS desenha a cobertura completa.
    features: list[FireHotspotFeature]


class FireHotspotDetails(CamelModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[FireHotspotFeature]
    matched_count: int


class FireMunicipality(CamelModel):
    ibge_code: str
    name: str
    state: str
    area_km2: float | None
    count: int
    count_24h: int = Field(
        validation_alias=AliasChoices("count_24h", "count24h", "count24H"),
        serialization_alias="count24h",
    )
    density: float | None
    latest_detection_at: datetime | None


class FireSummary(CamelModel):
    window_start: datetime
    window_end: datetime
    hours: int
    total: int
    municipalities: list[FireMunicipality]
    states: list[FireMunicipality] = Field(default_factory=list)
    ranked_municipalities: list[FireMunicipality] = Field(default_factory=list)
    unassigned_count: int = 0
    area_source: str = "Área geodésica calculada sobre a malha territorial original do IBGE"
