"""Contrato do endpoint de mapa.

A resposta é um GeoJSON FeatureCollection válido com a malha territorial e seus metadados.
"""

from __future__ import annotations

import enum
from typing import Any, Literal

from pydantic import SerializerFunctionWrapHandler, model_serializer

from app.models import GeometryLOD, TerritoryLevel
from app.schemas.common import CamelModel


class MapLod(str, enum.Enum):
    """Níveis de detalhe que a API aceita servir no mapa."""

    OVERVIEW = "overview"
    DETAIL = "detail"

    def to_geometry_lod(self) -> GeometryLOD:
        return GeometryLOD(self.value)


class MapScope(CamelModel):
    """Qual recorte territorial esta resposta representa."""

    level: TerritoryLevel
    parent: str | None = None
    lod: GeometryLOD
    count: int


class MapFeatureProperties(CamelModel):
    ibge_code: str
    name: str
    level: TerritoryLevel
    abbreviation: str | None = None
    parent_code: str | None = None
    parent_name: str | None = None


class MapFeature(CamelModel):
    type: Literal["Feature"] = "Feature"
    id: str
    properties: MapFeatureProperties
    geometry: dict[str, Any]


class MapFeatureCollection(CamelModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    scope: MapScope
    bbox: tuple[float, float, float, float] | None = None
    features: list[MapFeature]
    parent_feature: MapFeature | None = None
    next_offset: int | None = None

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Omite `bbox` quando não há extensão conhecida."""
        data: dict[str, Any] = handler(self)
        if data.get("bbox") is None:
            data.pop("bbox", None)
        return data
