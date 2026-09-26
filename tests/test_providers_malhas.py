"""Provider de malhas: extração de código e tipos geométricos mistos."""

import pytest
import respx
from httpx import Response

from app.core.errors import ProviderError
from app.providers.base import http_client
from app.providers.ibge import malhas
from tests.conftest import load_fixture


@respx.mock
async def test_malha_de_regioes_extrai_codarea_e_geometria() -> None:
    payload = load_fixture("malhas_regioes.json")
    route = respx.get("https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR").mock(
        return_value=Response(200, json=payload)
    )

    async with http_client("https://servicodados.ibge.gov.br") as client:
        records = await malhas.fetch_regions(client)

    assert route.called
    assert {record.ibge_code for record in records} == {"1", "2", "3", "4", "5"}
    assert all(record.geojson["type"] in {"Polygon", "MultiPolygon"} for record in records)


@respx.mock
async def test_qualidade_e_traduzida_para_o_vocabulario_atual_da_api() -> None:
    """A API deixou de aceitar `qualidade` numérica; hoje só aceita texto.

    Valores antigos como `4` retornam HTTP 400. A tradução fica isolada no
    provider justamente para que uma mudança dessas não vaze para o domínio.
    """
    payload = load_fixture("malhas_regioes.json")
    route = respx.get("https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR").mock(
        return_value=Response(200, json=payload)
    )

    async with http_client("https://servicodados.ibge.gov.br") as client:
        await malhas.fetch_states(client, quality="maxima")

    request = route.calls.last.request
    assert request.url.params["qualidade"] == "maxima"
    assert request.url.params["intrarregiao"] == "UF"
    assert request.url.params["formato"] == "application/vnd.geo+json"


@respx.mock
async def test_resposta_que_nao_e_featurecollection_falha_alto() -> None:
    respx.get("https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR").mock(
        return_value=Response(200, json={"statusCode": 400, "message": "parâmetro inválido"})
    )

    async with http_client("https://servicodados.ibge.gov.br") as client:
        with pytest.raises(ProviderError):
            await malhas.fetch_states(client)


@respx.mock
async def test_erro_http_da_fonte_vira_provider_error() -> None:
    """Uma resposta HTTP de erro da API de malhas do IBGE vira erro de provedor.

    Classificar como ProviderError permite à ingestão tratar o escopo como
    falho e seguir com os demais, em vez de abortar tudo.
    """
    respx.get("https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR").mock(
        return_value=Response(403, text="Forbidden")
    )

    async with http_client("https://servicodados.ibge.gov.br") as client:
        with pytest.raises(ProviderError) as error:
            await malhas.fetch_states(client)

    assert error.value.details["status"] == 403
