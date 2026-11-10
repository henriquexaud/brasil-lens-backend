from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class PlaceRecord:
    """Uma feição normalizada, pronta para a tabela `places`.

    Com `geometry` (GeoJSON) e sem coordenadas, o ponto é calculado no banco.
    `attributes` são pares (rótulo, valor) já em pt-BR, na ordem de exibição.
    """

    layer: str
    category: str | None = None
    # Categorias em que o lugar também entra, além da principal.
    extra_categories: tuple[str, ...] = ()
    external_id: str | None = None
    name: str | None = None
    summary: str | None = None
    attributes: list[tuple[str, str]] = field(default_factory=list)
    weight: float | None = None
    longitude: float | None = None
    latitude: float | None = None
    geometry: dict[str, Any] | None = None


def clean(value: Any) -> str | None:
    text = " ".join(str(value).split()) if value is not None else ""
    return text or None


def number(value: Any) -> float | None:
    try:
        result = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def pairs(*items: tuple[str, str | None]) -> list[tuple[str, str]]:
    return [(label, value) for label, value in items if value]


def area_label(hectares: float | None) -> str | None:
    if hectares is None or hectares <= 0:
        return None
    if hectares >= 100:
        return f"{hectares / 100:,.0f} km²".replace(",", ".")
    return f"{hectares:,.1f} ha".replace(",", "X").replace(".", ",").replace("X", ".")
