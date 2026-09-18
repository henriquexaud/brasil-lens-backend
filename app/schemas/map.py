"""Contrato do endpoint de mapa.

A resposta é um **GeoJSON FeatureCollection válido** com membros estrangeiros
(`scope`, `indicator`, `statistics`, `classification`). A especificação GeoJSON
permite membros extras no nível da coleção, e isso tem uma consequência prática
decisiva: o componente `<GeoJSON>` do react-leaflet consome a resposta **sem
nenhuma transformação**, enquanto os metadados de coropleta chegam no mesmo
payload. Era exatamente o objetivo — o frontend não busca indicador e geometria
separado para depois juntar.
"""

from __future__ import annotations

import enum
from typing import Any, Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer

from app.models import GeometryLOD, TerritoryLevel
from app.schemas.common import ApiDecimal, CamelModel


class MapLod(str, enum.Enum):
    """Níveis de detalhe que a API aceita servir.

    `canonical` existe no banco como verdade oficial e **não** aparece aqui: a
    malha municipal crua de Minas Gerais tem 8,8 MB, e expô-la em um parâmetro
    de consulta contradiria toda a estratégia de carregamento progressivo. O
    subconjunto público é, portanto, uma decisão de contrato, não um detalhe.
    """

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


class MapIndicatorMeta(CamelModel):
    key: str
    name: str
    unit: str
    decimal_places: int
    # Ano efetivamente usado. Quando o cliente pede `latest`, é aqui que ele
    # descobre qual ano respondeu — pode não ser o ano corrente.
    year: int | None
    requested_year: str
    available_years: list[int] = Field(default_factory=list)


class MapStatistics(CamelModel):
    min: ApiDecimal
    max: ApiDecimal
    mean: ApiDecimal
    median: ApiDecimal
    count: int
    # Territórios no escopo sem valor para o indicador/ano.
    missing: int


class MapClassification(CamelModel):
    method: Literal["quantile"]
    classes: int
    # Limite superior de cada classe. O frontend mapeia classe → cor.
    breaks: list[ApiDecimal]


class MapFeatureProperties(CamelModel):
    ibge_code: str
    name: str
    level: TerritoryLevel
    abbreviation: str | None = None
    parent_code: str | None = None
    parent_name: str | None = None
    value: ApiDecimal | None = None
    # Posição em [0,1] — calculada por consulta, nunca persistida.
    normalized_value: float | None = None
    class_index: int | None = None


class MapFeature(CamelModel):
    type: Literal["Feature"] = "Feature"
    # `id` é o código IBGE: identificador canônico, estável entre requisições.
    id: str
    properties: MapFeatureProperties
    geometry: dict[str, Any]


class MapFeatureCollection(CamelModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    scope: MapScope
    indicator: MapIndicatorMeta | None = None
    statistics: MapStatistics | None = None
    classification: MapClassification | None = None
    bbox: tuple[float, float, float, float] | None = None
    features: list[MapFeature]

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Omite `bbox` quando não há extensão conhecida.

        A RFC 7946 define `bbox` como opcional, mas exige que seja um array
        quando presente: `"bbox": null` é GeoJSON inválido e faz clientes
        tipados (incluindo o nosso) recusarem a resposta. Os demais campos
        nulos são mantidos de propósito — `value: null` é informação
        ("sem dado"), não ausência de campo.
        """
        data: dict[str, Any] = handler(self)
        if data.get("bbox") is None:
            data.pop("bbox", None)
        return data
