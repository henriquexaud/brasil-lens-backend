"""Tipos internos produzidos pelos providers — a fronteira do sistema.

Deliberadamente fora de `providers/ibge/`: são os tipos que **qualquer**
provider, de qualquer contexto, produz para atravessar a fronteira com o
domínio. A partir daqui ninguém sabe se o dado veio do IBGE, do SICONFI, do
INMET ou de um CSV. Adicionar uma fonte nova significa produzir estes mesmos
tipos — sem tocar em modelo, serviço ou API. Viver dentro de `ibge/` até agora
era em si um acoplamento: um provider novo teria que importar de dentro do
pacote de outra fonte para falar a mesma língua.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.models import TerritoryLevel


@dataclass(frozen=True, slots=True)
class TerritoryRecord:
    """Um território normalizado, ainda sem geometria."""

    ibge_code: str
    name: str
    level: TerritoryLevel
    parent_ibge_code: str | None
    abbreviation: str | None = None


@dataclass(frozen=True, slots=True)
class GeometryRecord:
    """Geometria canônica de um território, em GeoJSON (EPSG:4326)."""

    ibge_code: str
    geojson: dict[str, Any]


@dataclass(frozen=True, slots=True)
class IndicatorObservation:
    """Uma observação: território, ano e valor — já na unidade final.

    Os providers descartam sentinelas de indisponibilidade e aplicam o
    multiplicador da fonte (ex.: "Mil Reais" → BRL) antes de produzir isto.
    """

    ibge_code: str
    reference_year: int
    value: Decimal
