from __future__ import annotations

from typing import Literal

from app.schemas.common import CamelModel

RouteProfile = Literal["car", "bike", "foot"]


class Place(CamelModel):
    """Endereço ou lugar do OpenStreetMap (Photon), sempre no Brasil."""

    id: str
    name: str
    detail: str | None = None
    latitude: float
    longitude: float


class PlaceSearch(CamelModel):
    places: list[Place]


class RouteLeg(CamelModel):
    distance_m: float
    duration_s: float
    # Vias principais do trecho, como o OSRM resume ("BR-116, Avenida Brasil").
    summary: str | None = None


class RouteWaypoint(CamelModel):
    """Ponto pedido, encaixado na via mais próxima."""

    latitude: float
    longitude: float
    snap_distance_m: float
    name: str | None = None


class RouteResult(CamelModel):
    profile: RouteProfile
    distance_m: float
    duration_s: float
    # Polyline codificada com precisão 6 (formato do Google/OSRM), em lat,lon.
    geometry: str
    legs: list[RouteLeg]
    waypoints: list[RouteWaypoint]
