"""Contratos de território, incluindo a projeção de overview."""

from __future__ import annotations

from app.models import IndicatorOrigin, TerritoryLevel
from app.schemas.common import ApiDecimal, CamelModel, Pagination

# Extensão geográfica no formato do GeoJSON: [oeste, sul, leste, norte].
BoundingBox = tuple[float, float, float, float]


class TerritoryRef(CamelModel):
    """Referência mínima a outro território (pai, capital)."""

    ibge_code: str
    name: str
    level: TerritoryLevel | None = None


class TerritorySummary(CamelModel):
    ibge_code: str
    name: str
    level: TerritoryLevel
    abbreviation: str | None = None
    parent: TerritoryRef | None = None


class TerritoryListResponse(CamelModel):
    territories: list[TerritorySummary]
    pagination: Pagination


class TerritoryDetail(TerritorySummary):
    capital: TerritoryRef | None = None
    children_count: int
    children_level: TerritoryLevel | None = None
    bbox: BoundingBox | None = None


class IndicatorValueOut(CamelModel):
    """Um indicador já resolvido para exibição.

    `value` e `year` são nulos juntos quando não há dado: é a política única de
    ausência da API. O indicador continua presente para a interface poder
    mostrar "sem dado" em vez de esconder a linha.
    """

    key: str
    name: str
    unit: str
    decimal_places: int
    origin: IndicatorOrigin
    value: ApiDecimal | None
    year: int | None
    # Proveniência legível: de qual dataset/tabela veio este número.
    source: str | None = None


class TerritoryOverview(TerritoryDetail):
    """Resposta pronta para a tela de detalhe: um request, nenhuma junção no cliente."""

    indicators: list[IndicatorValueOut]
