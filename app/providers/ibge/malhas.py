"""Provider das malhas territoriais oficiais do IBGE (API de Malhas v3).

`https://servicodados.ibge.gov.br/api/v3/malhas`

Observações verificadas contra a API em produção:

* O parâmetro ``qualidade`` **deixou de ser numérico**: hoje aceita apenas
  ``minima``, ``intermediaria`` ou ``maxima``. Valores antigos (``4``) retornam
  HTTP 400. A tradução fica isolada aqui de propósito.
* As features vêm com a propriedade ``codarea`` contendo o código IBGE — e
  ``codarea`` do país é literalmente ``"BR"``, o mesmo token que usamos no nível
  raiz.
* Os tipos de geometria **misturam** ``Polygon`` e ``MultiPolygon`` na mesma
  resposta. A normalização para MultiPolygon acontece no banco (``ST_Multi``),
  não aqui: é o PostGIS que garante o tipo da coluna.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx

from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.providers.base import get_json
from app.providers.records import GeometryRecord

logger = get_logger(__name__)

SOURCE = "ibge"
DATASET_CODE = "malhas/v3"
DATASET_NAME = "IBGE — Malhas Territoriais (API de Malhas v3, qualidade máxima)"
DATASET_URL = "https://servicodados.ibge.gov.br/api/docs/malhas"

Quality = Literal["minima", "intermediaria", "maxima"]

_GEOJSON_FORMAT = "application/vnd.geo+json"


async def fetch_country(client: httpx.AsyncClient, quality: Quality = "maxima") -> GeometryRecord:
    """Contorno do Brasil (1 feature, `codarea="BR"`)."""
    features = await _fetch_features(client, "/api/v3/malhas/paises/BR", quality=quality)
    return _single(features, scope="paises/BR")


async def fetch_regions(
    client: httpx.AsyncClient, quality: Quality = "maxima"
) -> list[GeometryRecord]:
    """As 5 grandes regiões (`codarea` = 1..5)."""
    return await _fetch_features(
        client, "/api/v3/malhas/paises/BR", quality=quality, intrarregiao="regiao"
    )


async def fetch_states(
    client: httpx.AsyncClient, quality: Quality = "maxima"
) -> list[GeometryRecord]:
    """As 27 UFs (`codarea` = código da UF)."""
    return await _fetch_features(
        client, "/api/v3/malhas/paises/BR", quality=quality, intrarregiao="UF"
    )


async def fetch_municipalities_of_state(
    client: httpx.AsyncClient,
    state_ibge_code: str,
    quality: Quality = "maxima",
) -> list[GeometryRecord]:
    """Municípios de uma UF.

    O recorte por UF é o que torna a ingestão viável: a malha municipal completa
    do país passa de 60 MB, enquanto por UF fica entre ~1 MB e ~9 MB.
    """
    return await _fetch_features(
        client,
        f"/api/v3/malhas/estados/{state_ibge_code}",
        quality=quality,
        intrarregiao="municipio",
    )


async def _fetch_features(
    client: httpx.AsyncClient,
    path: str,
    *,
    quality: Quality,
    intrarregiao: str | None = None,
) -> list[GeometryRecord]:
    params: dict[str, Any] = {"formato": _GEOJSON_FORMAT, "qualidade": quality}
    if intrarregiao is not None:
        params["intrarregiao"] = intrarregiao

    payload = await get_json(client, path, params=params, source="IBGE Malhas")

    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise ProviderError(
            "IBGE Malhas não devolveu um FeatureCollection.",
            path=path,
            received=str(payload)[:300],
        )

    records: list[GeometryRecord] = []
    for feature in payload.get("features", []):
        code = (feature.get("properties") or {}).get("codarea")
        geometry = feature.get("geometry")
        if not code or not geometry:
            raise ProviderError(
                "Feature sem 'codarea' ou sem geometria na malha do IBGE.",
                path=path,
            )
        records.append(GeometryRecord(ibge_code=str(code), geojson=geometry))

    logger.info(
        "malhas.fetched",
        extra={"path": path, "intrarregiao": intrarregiao, "features": len(records)},
    )
    return records


def _single(records: list[GeometryRecord], *, scope: str) -> GeometryRecord:
    if len(records) != 1:
        raise ProviderError(
            f"Esperava exatamente 1 feature em {scope}, recebi {len(records)}.",
            scope=scope,
        )
    return records[0]
