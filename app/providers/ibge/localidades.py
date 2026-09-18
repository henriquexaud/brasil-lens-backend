"""Provider da API IBGE Localidades — hierarquia e nomes oficiais.

`https://servicodados.ibge.gov.br/api/v1/localidades`

Esta é a **única** fonte de nomes de território. A API de agregados devolve
nomes com sufixo de UF no nível municipal (ex.: "Adamantina - SP"), o que
contaminaria a busca e a exibição; por isso valores e nomes vêm de lugares
diferentes, casados pelo código IBGE.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.models import TerritoryLevel
from app.providers.base import get_json
from app.providers.ibge.records import TerritoryRecord

logger = get_logger(__name__)

SOURCE = "ibge"
DATASET_CODE = "localidades/v1"
DATASET_NAME = "IBGE — Divisão Territorial Brasileira (API Localidades v1)"
DATASET_URL = "https://servicodados.ibge.gov.br/api/docs/localidades"

# O IBGE não define código para o país; "BR" é o identificador que usamos no
# nível raiz e é o mesmo token aceito pela API de malhas (/malhas/paises/BR).
COUNTRY_CODE = "BR"
COUNTRY_NAME = "Brasil"


class _RawRegion(BaseModel):
    id: int
    sigla: str
    nome: str


class _RawState(BaseModel):
    id: int
    sigla: str
    nome: str
    regiao: _RawRegion


class _RawMunicipalityState(BaseModel):
    """Recorte de `municipio.microrregiao.mesorregiao.UF` usado para achar a UF."""

    id: int
    sigla: str


class _RawMesoregion(BaseModel):
    uf: _RawMunicipalityState = Field(alias="UF")


class _RawMicroregion(BaseModel):
    mesorregiao: _RawMesoregion


class _RawImmediateRegionState(BaseModel):
    id: int


class _RawIntermediateRegion(BaseModel):
    uf: _RawImmediateRegionState = Field(alias="UF")


class _RawImmediateRegion(BaseModel):
    regiao_intermediaria: _RawIntermediateRegion = Field(alias="regiao-intermediaria")


class _RawMunicipality(BaseModel):
    id: int
    nome: str
    # O IBGE expõe a UF por dois caminhos (divisão antiga e nova). Aceitamos
    # qualquer um dos dois: municípios novos às vezes só trazem um deles.
    microrregiao: _RawMicroregion | None = None
    regiao_imediata: _RawImmediateRegion | None = Field(default=None, alias="regiao-imediata")

    def state_code(self) -> str:
        if self.microrregiao is not None:
            return str(self.microrregiao.mesorregiao.uf.id)
        if self.regiao_imediata is not None:
            return str(self.regiao_imediata.regiao_intermediaria.uf.id)
        raise ProviderError(
            "Município sem UF identificável na resposta de Localidades.",
            municipality=self.id,
        )


def country_record() -> TerritoryRecord:
    return TerritoryRecord(
        ibge_code=COUNTRY_CODE,
        name=COUNTRY_NAME,
        level=TerritoryLevel.COUNTRY,
        parent_ibge_code=None,
    )


async def fetch_regions(client: httpx.AsyncClient) -> list[TerritoryRecord]:
    payload = await _get(client, "/api/v1/localidades/regioes")
    regions = [_RawRegion.model_validate(item) for item in payload]
    return [
        TerritoryRecord(
            ibge_code=str(region.id),
            name=region.nome,
            level=TerritoryLevel.REGION,
            parent_ibge_code=COUNTRY_CODE,
            abbreviation=region.sigla,
        )
        for region in regions
    ]


async def fetch_states(client: httpx.AsyncClient) -> list[TerritoryRecord]:
    payload = await _get(client, "/api/v1/localidades/estados")
    states = [_RawState.model_validate(item) for item in payload]
    return [
        TerritoryRecord(
            ibge_code=str(state.id),
            name=state.nome,
            level=TerritoryLevel.STATE,
            parent_ibge_code=str(state.regiao.id),
            abbreviation=state.sigla,
        )
        for state in states
    ]


async def fetch_municipalities(client: httpx.AsyncClient) -> list[TerritoryRecord]:
    """Todos os municípios do país em uma requisição (~5.570 registros)."""
    payload = await _get(client, "/api/v1/localidades/municipios")
    municipalities = [_RawMunicipality.model_validate(item) for item in payload]
    return [
        TerritoryRecord(
            ibge_code=str(municipality.id),
            name=municipality.nome,
            level=TerritoryLevel.MUNICIPALITY,
            parent_ibge_code=municipality.state_code(),
        )
        for municipality in municipalities
    ]


async def _get(client: httpx.AsyncClient, path: str) -> list[Any]:
    payload = await get_json(client, path, source="IBGE Localidades")
    if not isinstance(payload, list):
        raise ProviderError("IBGE Localidades devolveu payload inesperado.", path=path)
    logger.info("localidades.fetched", extra={"path": path, "records": len(payload)})
    return payload
