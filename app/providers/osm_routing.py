"""Endereços (Photon) e rotas (OSRM) sobre dados do OpenStreetMap (ADR-24)."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.errors import (
    ProviderError,
    ProviderRateLimitedError,
    RouteNotFoundError,
    RoutePointUnreachableError,
)
from app.schemas.routing import Place, RouteLeg, RouteProfile, RouteResult, RouteWaypoint

# Oeste, sul, leste, norte; inclui Fernando de Noronha (Trindade fica de fora).
BRAZIL_BBOX = (-74.1, -33.9, -32.2, 5.4)

UF = {
    "Acre": "AC",
    "Alagoas": "AL",
    "Amapá": "AP",
    "Amazonas": "AM",
    "Bahia": "BA",
    "Ceará": "CE",
    "Distrito Federal": "DF",
    "Espírito Santo": "ES",
    "Goiás": "GO",
    "Maranhão": "MA",
    "Mato Grosso": "MT",
    "Mato Grosso do Sul": "MS",
    "Minas Gerais": "MG",
    "Pará": "PA",
    "Paraíba": "PB",
    "Paraná": "PR",
    "Pernambuco": "PE",
    "Piauí": "PI",
    "Rio de Janeiro": "RJ",
    "Rio Grande do Norte": "RN",
    "Rio Grande do Sul": "RS",
    "Rondônia": "RO",
    "Roraima": "RR",
    "Santa Catarina": "SC",
    "São Paulo": "SP",
    "Sergipe": "SE",
    "Tocantins": "TO",
}

RATE_LIMITED = (
    "Muitas consultas ao serviço de {what} do OpenStreetMap. Tente novamente em instantes."
)


def _check(response: httpx.Response, what: str) -> None:
    if response.status_code == 429:
        retry = response.headers.get("retry-after", "")
        raise ProviderRateLimitedError(
            RATE_LIMITED.format(what=what), int(retry) if retry.isdigit() else 30
        )


def _place(feature: dict[str, Any]) -> Place | None:
    props = feature.get("properties") or {}
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    if props.get("countrycode") != "BR" or len(coords) != 2:
        return None
    street = props.get("street")
    number = props.get("housenumber")
    address = f"{street}, {number}" if street and number else street
    city = props.get("city")
    name = props.get("name") or address or city or props.get("state")
    if not name:
        return None
    state = props.get("state")
    uf = UF.get(state or "", state)
    parts = [address if address != name else None, props.get("district"), city, uf]
    seen: set[str] = {name}
    detail = []
    for part in parts:
        if part and part not in seen:
            seen.add(part)
            detail.append(part)
    return Place(
        id=f"{props.get('osm_type', '')}{props.get('osm_id', '')}",
        name=name,
        detail=", ".join(detail) or None,
        latitude=round(coords[1], 6),
        longitude=round(coords[0], 6),
    )


def _places(payload: Any, limit: int) -> list[Place]:
    places: list[Place] = []
    labels: set[tuple[str, str | None]] = set()
    for feature in payload.get("features", []) if isinstance(payload, dict) else []:
        place = _place(feature)
        # O OSM costuma ter a cidade como nó e como limite: um item só.
        if place is None or (place.name, place.detail) in labels:
            continue
        labels.add((place.name, place.detail))
        places.append(place)
    return places[:limit]


async def search_places(
    client: httpx.AsyncClient,
    query: str,
    *,
    near: tuple[float, float] | None = None,
    limit: int = 6,
) -> list[Place]:
    params: dict[str, Any] = {
        "q": query,
        "limit": limit + 4,
        "bbox": ",".join(str(value) for value in BRAZIL_BBOX),
    }
    if near:
        params["lat"], params["lon"] = near
    try:
        response = await client.get("/api", params=params)
        _check(response, "endereços")
        response.raise_for_status()
        return _places(response.json(), limit)
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError("Não foi possível buscar endereços no OpenStreetMap.") from exc


async def reverse_place(
    client: httpx.AsyncClient, latitude: float, longitude: float
) -> Place | None:
    try:
        response = await client.get("/reverse", params={"lat": latitude, "lon": longitude})
        _check(response, "endereços")
        response.raise_for_status()
        places = _places(response.json(), 1)
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError("Não foi possível identificar o endereço no OpenStreetMap.") from exc
    return places[0] if places else None


def _summary(raw: str | None) -> str | None:
    # O OSRM repete a via quando ela volta a ser a mais longa do trecho.
    names = list(dict.fromkeys(part.strip() for part in (raw or "").split(",") if part.strip()))
    return ", ".join(names) or None


async def fetch_route(
    client: httpx.AsyncClient, profile: RouteProfile, points: list[tuple[float, float]]
) -> RouteResult:
    """`points` em (latitude, longitude), na ordem da viagem."""
    path = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    try:
        response = await client.get(
            f"/routed-{profile}/route/v1/driving/{path}",
            # Os passos só servem para o resumo de vias de cada trecho.
            params={"overview": "full", "geometries": "polyline6", "steps": "true"},
        )
        _check(response, "rotas")
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError("Não foi possível calcular a rota no OpenStreetMap.") from exc
    code = payload.get("code") if isinstance(payload, dict) else None
    if code == "NoRoute":
        raise RouteNotFoundError()
    if code == "NoSegment":
        raise RoutePointUnreachableError()
    if code != "Ok" or not payload.get("routes"):
        raise ProviderError(
            "O serviço de rotas do OpenStreetMap respondeu de forma inesperada.",
            status=response.status_code,
            osrmCode=code,
        )
    try:
        route = payload["routes"][0]
        return RouteResult(
            profile=profile,
            distance_m=round(route["distance"], 1),
            duration_s=round(route["duration"], 1),
            geometry=route["geometry"],
            legs=[
                RouteLeg(
                    distance_m=round(leg["distance"], 1),
                    duration_s=round(leg["duration"], 1),
                    summary=_summary(leg.get("summary")),
                )
                for leg in route["legs"]
            ],
            waypoints=[
                RouteWaypoint(
                    latitude=round(point["location"][1], 6),
                    longitude=round(point["location"][0], 6),
                    snap_distance_m=round(point.get("distance") or 0, 1),
                    name=point.get("name") or None,
                )
                for point in payload["waypoints"]
            ],
        )
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("O serviço de rotas do OpenStreetMap respondeu incompleto.") from exc
