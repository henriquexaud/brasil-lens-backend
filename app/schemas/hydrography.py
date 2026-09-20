"""Contrato do endpoint de hidrografia (rios, lagos e corpos d'água).

GeoJSON FeatureCollection válido trazendo cursos d'água lineares e massas d'água
poligonais com propriedades padronizadas da Agência Nacional de Águas (ANA / SNIRH).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import SerializerFunctionWrapHandler, model_serializer

from app.schemas.common import CamelModel

HydroCategory = Literal["river", "water_body"]


class HydroFeatureProperties(CamelModel):
    id: str
    name: str
    category: HydroCategory
    drainage_area_km2: float | None = None
    dominion: str | None = None
    management: str | None = None
    body_type: str | None = None
    segment_count: int | None = None


class HydroFeature(CamelModel):
    type: Literal["Feature"] = "Feature"
    id: str
    properties: HydroFeatureProperties
    geometry: dict[str, Any]
    bbox: tuple[float, float, float, float] | None = None



class HydroMetadata(CamelModel):
    level: str
    parent_code: str | None = None
    river_count: int
    water_body_count: int
    source: str = "ANA - Agência Nacional de Águas e Saneamento Básico / SNIRH"


class HydroFeatureCollection(CamelModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    metadata: HydroMetadata
    bbox: tuple[float, float, float, float] | None = None
    features: list[HydroFeature]

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if data.get("bbox") is None:
            data.pop("bbox", None)
        return data

