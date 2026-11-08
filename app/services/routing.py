"""Busca de endereços e rotas entre pontos do Brasil (ADR-24).

Os servidores públicos do OSM são de uso justo: as respostas ficam em cache e as
chamadas simultâneas a cada um são poucas.
"""

from __future__ import annotations

import asyncio

import httpx

from app.core.cache import TTLCache
from app.core.config import settings
from app.core.errors import InvalidParameterError
from app.core.text import normalize_text
from app.providers import osm_routing as provider
from app.schemas.routing import Place, PlaceSearch, RouteProfile, RouteResult

MAX_POINTS = 7  # origem, até cinco paradas e destino
MIN_QUERY_LENGTH = 3
USER_AGENT = "brasil-lens/0.1 (+https://www.brasillens.com.br)"

_places_cache: TTLCache[list[Place]] = TTLCache(ttl_seconds=24 * 3600, max_entries=1024)
_reverse_cache: TTLCache[list[Place]] = TTLCache(ttl_seconds=24 * 3600, max_entries=512)
_route_cache: TTLCache[RouteResult] = TTLCache(ttl_seconds=3600, max_entries=256)
_photon_slots = asyncio.Semaphore(2)
_osrm_slots = asyncio.Semaphore(2)


def _client(base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=settings.routing_http_timeout,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt"},
    )


def _inside_brazil(latitude: float, longitude: float) -> bool:
    west, south, east, north = provider.BRAZIL_BBOX
    return west <= longitude <= east and south <= latitude <= north


async def search_places(
    query: str, near: tuple[float, float] | None = None, limit: int = 6
) -> PlaceSearch:
    text = " ".join(query.split())
    if len(text) < MIN_QUERY_LENGTH:
        return PlaceSearch(places=[])
    # A proximidade só ordena os resultados; arredondada, várias telas dividem o cache.
    bias = (round(near[0], 1), round(near[1], 1)) if near else None
    key = (normalize_text(text), bias, limit)
    cached = _places_cache.get(key)
    if cached is None:
        async with _photon_slots, _client(settings.photon_url) as client:
            cached = await provider.search_places(client, text, near=bias, limit=limit)
        _places_cache.set(key, cached)
    return PlaceSearch(places=cached)


async def reverse_place(latitude: float, longitude: float) -> Place | None:
    if not _inside_brazil(latitude, longitude):
        return None
    key = (round(latitude, 4), round(longitude, 4))
    cached = _reverse_cache.get(key)
    if cached is None:
        async with _photon_slots, _client(settings.photon_url) as client:
            place = await provider.reverse_place(client, latitude, longitude)
        # Lista vazia guarda o "nada aqui" sem confundir com ausência no cache.
        cached = [place] if place else []
        _reverse_cache.set(key, cached)
    return cached[0] if cached else None


def parse_points(raw: str) -> list[tuple[float, float]]:
    """`lat,lon;lat,lon;...` → pontos validados, na ordem da viagem."""
    points: list[tuple[float, float]] = []
    for chunk in raw.split(";"):
        try:
            latitude, longitude = (float(value) for value in chunk.split(","))
        except ValueError as exc:
            raise InvalidParameterError(
                "Pontos devem vir como 'latitude,longitude' separados por ';'.", "points"
            ) from exc
        if not _inside_brazil(latitude, longitude):
            raise InvalidParameterError("Os pontos da rota precisam ficar no Brasil.", "points")
        points.append((latitude, longitude))
    if not 2 <= len(points) <= MAX_POINTS:
        raise InvalidParameterError(
            f"Uma rota tem de 2 a {MAX_POINTS} pontos (origem, paradas e destino).", "points"
        )
    return points


async def get_route(profile: RouteProfile, points: list[tuple[float, float]]) -> RouteResult:
    # ~1 m: o mesmo ponto clicado de novo reaproveita a rota.
    key = (profile, tuple((round(lat, 5), round(lon, 5)) for lat, lon in points))
    cached = _route_cache.get(key)
    if cached is None:
        async with _osrm_slots, _client(settings.osrm_url) as client:
            cached = await provider.fetch_route(client, profile, list(key[1]))
        _route_cache.set(key, cached)
    return cached
