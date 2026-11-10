"""Esportes: locais de prática mapeados no OpenStreetMap.

Não há cadastro federal de quadras, pistas e piscinas públicas com localização;
o OSM é a fonte já usada no Transporte (ADR-18). `scripts/sports_places/build.sh`
filtra o extrato do Brasil e exporta um GeoJSON por linha, lido aqui.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

import orjson

from app.core.errors import ProviderError
from app.providers.place_record import PlaceRecord, clean, pairs

SOURCE = "OpenStreetMap"
DATASET_URL = "https://download.geofabrik.de/south-america/brazil.html"

_SPORTS = {
    "soccer": "soccer",
    "futsal": "soccer",
    "five-a-side": "soccer",
    "beachsoccer": "soccer",
    "basketball": "basketball",
    "streetball": "basketball",
    "volleyball": "volleyball",
    "beachvolleyball": "volleyball",
    "footvolley": "volleyball",
    "tennis": "tennis",
    "beach_tennis": "tennis",
    "padel": "tennis",
    "skateboard": "skate",
    "roller_skating": "skate",
    "bmx": "skate",
    "swimming": "swimming",
    "athletics": "athletics",
    "running": "athletics",
    "fitness": "fitness",
    "calisthenics": "fitness",
    "handball": "multi",
    "multi": "multi",
}
_COURT_SPORTS = frozenset({"soccer", "basketball", "volleyball", "multi"})
_SPORT_LABELS = {
    "soccer": "Futebol",
    "futsal": "Futsal",
    "five-a-side": "Futebol society",
    "beachsoccer": "Futebol de areia",
    "basketball": "Basquete",
    "streetball": "Basquete de rua",
    "volleyball": "Vôlei",
    "beachvolleyball": "Vôlei de praia",
    "footvolley": "Futevôlei",
    "tennis": "Tênis",
    "beach_tennis": "Beach tennis",
    "padel": "Padel",
    "skateboard": "Skate",
    "roller_skating": "Patinação",
    "bmx": "BMX",
    "swimming": "Natação",
    "athletics": "Atletismo",
    "running": "Corrida",
    "fitness": "Ginástica",
    "calisthenics": "Calistenia",
    "handball": "Handebol",
    "multi": "Poliesportiva",
}
_SURFACES = {
    "grass": "Grama",
    "artificial_turf": "Grama sintética",
    "sand": "Areia",
    "concrete": "Concreto",
    "asphalt": "Asfalto",
    "paved": "Pavimentado",
    "clay": "Saibro",
    "wood": "Madeira",
    "tartan": "Emborrachado",
    "dirt": "Terra",
    "earth": "Terra",
    "ground": "Terra",
    "acrylic": "Acrílico",
}
_PRIVATE = frozenset({"private", "no", "members", "residents"})
_OPEN_POOL = frozenset({"yes", "public", "permissive"})
_YES_NO = {"yes": "Sim", "no": "Não"}


def _positions(node: Any) -> Iterator[tuple[float, float]]:
    if isinstance(node, list) and len(node) >= 2 and isinstance(node[0], int | float):
        yield float(node[0]), float(node[1])
    elif isinstance(node, list):
        for child in node:
            yield from _positions(child)


def _center(geometry: dict[str, Any]) -> tuple[float, float] | None:
    points = list(_positions(geometry.get("coordinates")))
    if not points:
        return None
    longitudes = [point[0] for point in points]
    latitudes = [point[1] for point in points]
    return (
        round((min(longitudes) + max(longitudes)) / 2, 6),
        round((min(latitudes) + max(latitudes)) / 2, 6),
    )


def _groups(tags: dict[str, Any]) -> set[str]:
    sports = (item.strip().lower() for item in str(tags.get("sport", "")).split(";"))
    return {_SPORTS[item] for item in sports if item in _SPORTS}


def category(tags: dict[str, Any]) -> str | None:
    """Categoria do local, ou `None` quando a etiqueta não diz o que se pratica ali."""
    leisure = tags.get("leisure")
    if str(tags.get("access", "")).lower() in _PRIVATE:
        return None
    groups = _groups(tags)
    if leisure == "skatepark":
        return "skate"
    if leisure == "fitness_station":
        return "fitness"
    if leisure == "swimming_pool":
        # Piscina sem acesso declarado é quase sempre de casa ou condomínio.
        return "swimming" if str(tags.get("access", "")).lower() in _OPEN_POOL else None
    if leisure == "track":
        return "athletics" if not groups or "athletics" in groups else None
    if leisure == "stadium":
        return "stadium"
    if leisure == "sports_centre":
        if groups == {"swimming"}:
            return "swimming"
        return "centre"
    if leisure == "pitch":
        if not groups:
            return None
        # Mais de um esporte de quadra no mesmo piso é a quadra poliesportiva.
        if len(groups) > 1:
            return "multi" if groups <= _COURT_SPORTS else next(iter(sorted(groups)))
        return next(iter(groups))
    return None


def place(feature: dict[str, Any]) -> PlaceRecord | None:
    tags = feature.get("properties") or {}
    kind = category(tags)
    center = _center(feature.get("geometry") or {})
    if kind is None or center is None:
        return None
    sports = [
        item.strip().lower() for item in str(tags.get("sport", "")).split(";") if item.strip()
    ]
    labels = ", ".join(
        dict.fromkeys(_SPORT_LABELS[item] for item in sports if item in _SPORT_LABELS)
    )
    surface = _SURFACES.get(str(tags.get("surface", "")).lower())
    return PlaceRecord(
        layer="sport",
        category=kind,
        # Quem procura basquete também quer a poliesportiva e o centro que o oferecem.
        extra_categories=tuple(sorted(_groups(tags) - {kind, "multi"})),
        external_id=clean(feature.get("id")),
        name=clean(tags.get("name")),
        summary=" · ".join(part for part in (labels or None, surface) if part) or None,
        attributes=pairs(
            ("Esportes", labels or None),
            ("Piso", surface),
            ("Iluminação", _YES_NO.get(str(tags.get("lit", "")).lower())),
            ("Coberta", _YES_NO.get(str(tags.get("covered", "")).lower())),
            ("Horário", clean(tags.get("opening_hours"))),
        ),
        longitude=center[0],
        latitude=center[1],
    )


def read(lines: Iterable[bytes]) -> Iterator[PlaceRecord]:
    """Lê o GeoJSON por linha do `osmium export` (com ou sem o separador RS)."""
    for raw in lines:
        line = raw.strip().lstrip(b"\x1e")
        if not line:
            continue
        try:
            feature = orjson.loads(line)
        except orjson.JSONDecodeError as exc:
            raise ProviderError("Arquivo de esportes do OSM em formato inesperado.") from exc
        record = place(feature) if isinstance(feature, dict) else None
        if record is not None:
            yield record
