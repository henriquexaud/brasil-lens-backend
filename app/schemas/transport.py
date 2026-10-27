from __future__ import annotations

from typing import Any, Literal

from app.schemas.common import CamelModel

TransportLayer = Literal["road_federal", "road_state", "road_local", "rail", "waterway", "port"]
TransportDetail = Literal["overview", "regional", "local", "street"]


class TransportFeatureProperties(CamelModel):
    layer: TransportLayer
    name: str | None = None
    attributes: dict[str, str] | None = None


class TransportFeature(CamelModel):
    type: Literal["Feature"] = "Feature"
    id: str
    properties: TransportFeatureProperties
    geometry: dict[str, Any]


class TransportNetworkMetadata(CamelModel):
    layer: TransportLayer
    detail: TransportDetail
    version: int


class TransportNetwork(CamelModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    metadata: TransportNetworkMetadata
    features: list[TransportFeature]


class TransportIdentify(CamelModel):
    feature: TransportFeatureProperties | None


class TransportSource(CamelModel):
    key: Literal["dnit", "ibge", "osm"]
    label: str
    edition: str | None = None
    description: str
    url: str | None = None


class TransportLayerInfo(CamelModel):
    key: TransportLayer
    label: str
    source: Literal["dnit", "ibge"]
    min_zoom: float


class TransportTiles(CamelModel):
    """Tiles vetoriais OSM; `url` é relativo à API e leva a versão como cache."""

    url: str
    min_zoom: int
    max_zoom: int
    description: str


class TransportCatalog(CamelModel):
    version: int
    layers: list[TransportLayerInfo]
    sources: list[TransportSource]
    tiles: TransportTiles | None


class TransportTotal(CamelModel):
    layer: TransportLayer
    value: float | None


class TransportSummary(CamelModel):
    code: str
    name: str
    totals: list[TransportTotal]
