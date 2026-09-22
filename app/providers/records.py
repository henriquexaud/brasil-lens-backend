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

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.models import TerritoryLevel, WeatherStationType


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


@dataclass(frozen=True, slots=True)
class WeatherStationRecord:
    """Uma estação/pluviômetro, identificado na fonte — sem leitura ainda."""

    provider: str
    external_code: str
    name: str
    station_type: WeatherStationType
    latitude: float
    longitude: float
    state_abbreviation: str | None = None


@dataclass(frozen=True, slots=True)
class WeatherObservationRecord:
    """Uma leitura de estação, já normalizada (sentinelas descartadas).

    Todos os campos de medida são opcionais: uma estação de chuva do CEMADEN
    só preenche `precipitation_mm`.
    """

    provider: str
    external_code: str
    observed_at: datetime
    temperature_c: Decimal | None = None
    humidity_pct: Decimal | None = None
    pressure_hpa: Decimal | None = None
    precipitation_mm: Decimal | None = None


@dataclass(frozen=True, slots=True)
class WeatherAlertRecord:
    """Um alerta georreferenciado, já normalizado.

    `provider` é texto livre (não o enum `WeatherProvider`) de propósito: é a
    mesma fronteira descrita no docstring do módulo — um provider novo não
    deveria precisar importar um tipo de dentro de `app.models` só para se
    anunciar. `description` é opcional porque nem toda fonte tem uma frase
    livre além de `event`/`risks` (o INMET não tem; ver
    `app/providers/inmet/alerts.py`); quem tem (o CEMADEN, onde `event` sozinho
    não diz o município) preenche.
    """

    provider: str
    external_id: str
    event: str
    severity: str
    onset: datetime
    expires: datetime
    polygon_geojson: dict[str, Any]
    color: str | None = None
    description: str | None = None
    affected_ibge_codes: tuple[str, ...] = field(default_factory=tuple)
    risks: tuple[str, ...] = field(default_factory=tuple)
    instructions: tuple[str, ...] = field(default_factory=tuple)
