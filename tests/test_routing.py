from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.errors import (
    InvalidParameterError,
    ProviderRateLimitedError,
    RouteNotFoundError,
    RoutePointUnreachableError,
)
from app.providers import osm_routing as provider
from app.services import routing


def _feature(**props: Any) -> dict[str, Any]:
    coords = props.pop("coords", [-46.6519, -23.5649])
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": coords},
        "properties": {"countrycode": "BR", "osm_type": "N", "osm_id": 1, **props},
    }


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://osm.test")


def _osrm_ok() -> dict[str, Any]:
    return {
        "code": "Ok",
        "routes": [
            {
                "distance": 436443.84,
                "duration": 20335.7,
                "geometry": "abc",
                "legs": [{"distance": 436443.84, "duration": 20335.7, "summary": "BR-116, BR-116"}],
            }
        ],
        "waypoints": [
            {"location": [-46.65, -23.56], "distance": 9.5, "name": "Avenida Paulista"},
            {"location": [-43.17, -22.9], "distance": 6.37, "name": ""},
        ],
    }


async def test_endereco_vira_rua_numero_e_detalhe_com_sigla_da_uf() -> None:
    payload = {
        "features": [
            _feature(
                street="Avenida Paulista",
                housenumber="1000",
                district="Bela Vista",
                city="São Paulo",
                state="São Paulo",
            ),
            _feature(name="MASP", street="Av. Paulista", city="São Paulo", state="São Paulo"),
            _feature(name="Buenos Aires", countrycode="AR"),
        ]
    }
    async with _client(lambda _: httpx.Response(200, json=payload)) as client:
        places = await provider.search_places(client, "paulista")

    assert [(p.name, p.detail) for p in places] == [
        ("Avenida Paulista, 1000", "Bela Vista, São Paulo, SP"),
        ("MASP", "Av. Paulista, São Paulo, SP"),
    ]
    assert places[0].latitude == -23.5649


async def test_cidade_repetida_como_no_e_limite_aparece_uma_vez() -> None:
    payload = {
        "features": [
            _feature(name="Campinas", state="São Paulo"),
            _feature(name="Campinas", state="São Paulo", osm_type="R"),
            _feature(name="Campinas", state="Bahia"),
        ]
    }
    async with _client(lambda _: httpx.Response(200, json=payload)) as client:
        places = await provider.search_places(client, "campinas")

    assert [p.detail for p in places] == ["SP", "BA"]


async def test_busca_restringe_ao_brasil_e_ordena_pela_proximidade() -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"features": []})

    async with _client(handler) as client:
        await provider.search_places(client, "rua", near=(-23.5, -46.6))

    assert seen[0].params["bbox"] == "-74.1,-33.9,-32.2,5.4"
    assert seen[0].params["lat"] == "-23.5"


async def test_rota_resume_trechos_e_pontos_encaixados() -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(request.url.path)
        return httpx.Response(200, json=_osrm_ok())

    async with _client(handler) as client:
        route = await provider.fetch_route(client, "bike", [(-23.56, -46.65), (-22.9, -43.17)])

    assert urls == ["/routed-bike/route/v1/driving/-46.650000,-23.560000;-43.170000,-22.900000"]
    assert route.distance_m == 436443.8
    assert route.legs[0].summary == "BR-116"
    assert route.waypoints[1].name is None
    assert route.model_dump(by_alias=True)["distanceM"] == 436443.8


@pytest.mark.parametrize(
    ("code", "error"),
    [("NoRoute", RouteNotFoundError), ("NoSegment", RoutePointUnreachableError)],
)
async def test_sem_caminho_vira_erro_de_dominio(code: str, error: type[Exception]) -> None:
    async with _client(lambda _: httpx.Response(400, json={"code": code})) as client:
        with pytest.raises(error):
            await provider.fetch_route(client, "car", [(-23.5, -46.6), (-3.1, -60.0)])


async def test_cota_esgotada_vira_503_com_espera() -> None:
    response = httpx.Response(429, headers={"retry-after": "12"})
    async with _client(lambda _: response) as client:
        with pytest.raises(ProviderRateLimitedError) as caught:
            await provider.fetch_route(client, "car", [(-23.5, -46.6), (-22.9, -43.1)])

    assert caught.value.retry_after_seconds == 12


def test_pontos_fora_do_brasil_ou_em_excesso_sao_recusados() -> None:
    assert routing.parse_points("-23.5,-46.6;-22.9,-43.1") == [(-23.5, -46.6), (-22.9, -43.1)]
    for raw in ("-23.5,-46.6", "-34.6,-58.4;-23.5,-46.6", "x;y", ";".join(["-23.5,-46.6"] * 8)):
        with pytest.raises(InvalidParameterError):
            routing.parse_points(raw)


async def test_rota_repetida_sai_do_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    original = provider.fetch_route

    async def fake(_client: Any, profile: Any, points: Any) -> Any:
        calls.append(1)
        async with _client_ok() as client:
            return await original(client, profile, points)

    monkeypatch.setattr(provider, "fetch_route", fake)
    routing._route_cache.clear()
    points = [(-23.560001, -46.65), (-22.9, -43.17)]
    await routing.get_route("car", points)
    await routing.get_route("car", [(-23.560002, -46.65), (-22.9, -43.17)])

    assert len(calls) == 1


def _client_ok() -> httpx.AsyncClient:
    return _client(lambda _: httpx.Response(200, json=_osrm_ok()))


async def test_busca_curta_nao_consulta_a_fonte(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_: Any, **__: Any) -> Any:
        raise AssertionError("não deveria consultar")

    monkeypatch.setattr(provider, "search_places", boom)
    assert (await routing.search_places("sp")).places == []
