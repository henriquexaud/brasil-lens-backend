"""Consultas do mapa contra o banco real.

São os testes que protegem o contrato do produto: escopo correto, LOD correto,
resolução de `latest`, e territórios sem dado ainda desenháveis.

Rodam sobre os dados ingeridos (`make ingest`) e são pulados com mensagem clara
se o banco estiver vazio.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import (
    IndicatorNotFoundError,
    InvalidParameterError,
    TerritoryNotFoundError,
)
from app.models import GeometryLOD, TerritoryLevel
from app.schemas.map import MapLod
from app.services import indicators as indicators_service
from app.services import map as map_service
from app.services import territories as territories_service

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _isolated_cache() -> None:
    """Sem isto, um teste veria a resposta cacheada por outro."""
    map_service.clear_cache()
    indicators_service.clear_cache()


@asynccontextmanager
async def _api(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP da API usando a sessão do teste (o pool global é de outro loop)."""
    from app.api.deps import get_session
    from app.main import app

    async def override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


async def _require_ingested_data(session: AsyncSession) -> None:
    states = (
        await session.execute(text("SELECT COUNT(*) FROM territories WHERE level = 'state'"))
    ).scalar_one()
    if states == 0:
        pytest.skip("Banco sem dados. Rode 'make ingest' antes dos testes de integração.")


async def test_visao_inicial_traz_as_27_ufs_em_geometria_otimizada(
    session: AsyncSession,
) -> None:
    await _require_ingested_data(session)

    collection = await map_service.get_map(
        session, level=TerritoryLevel.STATE, indicator_key="population"
    )

    assert collection.type == "FeatureCollection"
    assert collection.scope.count == 27
    # A visão do país inteiro nunca serve geometria canônica; as UFs usam a
    # intermediária porque o mesmo contorno emoldura o estado aberto.
    assert collection.scope.lod is GeometryLOD.DETAIL
    assert len(collection.features) == 27
    assert {feature.properties.abbreviation for feature in collection.features} >= {"SP", "DF"}


async def test_drill_down_carrega_apenas_os_municipios_daquela_uf(
    session: AsyncSession,
) -> None:
    """O requisito central de performance: entrar em SP não traz MG."""
    await _require_ingested_data(session)

    collection = await map_service.get_map(
        session,
        level=TerritoryLevel.MUNICIPALITY,
        parent_code="35",
        indicator_key="population",
    )

    assert collection.scope.lod is GeometryLOD.DETAIL
    assert collection.scope.count == 645  # municípios de São Paulo
    assert all(feature.properties.parent_code == "35" for feature in collection.features)


async def test_municipios_sem_parent_sao_recusados(session: AsyncSession) -> None:
    """Proteção de payload aplicada pela API, não confiada ao cliente."""
    await _require_ingested_data(session)

    with pytest.raises(InvalidParameterError):
        await map_service.get_map(session, level=TerritoryLevel.MUNICIPALITY)


async def test_parent_de_nivel_incompativel_e_recusado(session: AsyncSession) -> None:
    await _require_ingested_data(session)

    with pytest.raises(InvalidParameterError):
        # O pai de um município é uma UF, não uma região.
        await map_service.get_map(session, level=TerritoryLevel.MUNICIPALITY, parent_code="3")


async def test_parent_inexistente_resulta_em_404(session: AsyncSession) -> None:
    await _require_ingested_data(session)

    with pytest.raises(TerritoryNotFoundError):
        await map_service.get_map(session, level=TerritoryLevel.MUNICIPALITY, parent_code="99")


async def test_indicador_inexistente_resulta_em_404(session: AsyncSession) -> None:
    await _require_ingested_data(session)

    with pytest.raises(IndicatorNotFoundError):
        await map_service.get_map(session, level=TerritoryLevel.STATE, indicator_key="nao_existe")


async def test_latest_resolve_para_o_ultimo_ano_com_dado_do_indicador(
    session: AsyncSession,
) -> None:
    """`latest` não é "ano corrente": é o último ano publicado daquele indicador.

    Indicadores diferentes resolvem para anos diferentes — é por isso que a
    resposta sempre carrega o ano que respondeu.
    """
    await _require_ingested_data(session)

    gdp = await map_service.get_map(
        session, level=TerritoryLevel.STATE, indicator_key="gdp", year="latest"
    )
    area = await map_service.get_map(
        session, level=TerritoryLevel.STATE, indicator_key="area_km2", year="latest"
    )

    assert gdp.indicator is not None and area.indicator is not None
    assert gdp.indicator.year is not None
    assert area.indicator.year is not None
    assert gdp.indicator.year != area.indicator.year
    assert gdp.indicator.year == max(gdp.indicator.available_years)


async def test_ano_explicito_e_respeitado(session: AsyncSession) -> None:
    await _require_ingested_data(session)

    collection = await map_service.get_map(
        session, level=TerritoryLevel.STATE, indicator_key="population", year="2022"
    )

    assert collection.indicator is not None
    assert collection.indicator.year == 2022
    assert collection.indicator.requested_year == "2022"


async def test_ano_sem_dado_ainda_desenha_o_mapa(session: AsyncSession) -> None:
    """Política de ausência: sem dado o território aparece, com valor nulo."""
    await _require_ingested_data(session)

    collection = await map_service.get_map(
        session, level=TerritoryLevel.STATE, indicator_key="gdp", year="1950"
    )

    assert collection.scope.count == 27
    assert collection.statistics is None
    assert collection.classification is None
    assert all(feature.properties.value is None for feature in collection.features)
    assert all(feature.properties.class_index is None for feature in collection.features)
    # A geometria continua presente — o país precisa ser desenhado.
    assert all(feature.geometry.get("coordinates") for feature in collection.features)


async def test_sem_indicador_devolve_apenas_geometria(session: AsyncSession) -> None:
    await _require_ingested_data(session)

    collection = await map_service.get_map(session, level=TerritoryLevel.STATE)

    assert collection.indicator is None
    assert collection.statistics is None
    assert collection.scope.count == 27
    assert all(feature.geometry for feature in collection.features)


async def test_estatisticas_e_classes_sao_coerentes_com_os_valores(
    session: AsyncSession,
) -> None:
    await _require_ingested_data(session)

    collection = await map_service.get_map(
        session, level=TerritoryLevel.STATE, indicator_key="gdp_per_capita"
    )

    assert collection.statistics is not None
    assert collection.classification is not None
    values = [f.properties.value for f in collection.features if f.properties.value is not None]
    assert collection.statistics.min == min(values)
    assert collection.statistics.max == max(values)
    assert collection.statistics.count == len(values)
    assert collection.classification.breaks == sorted(collection.classification.breaks)
    assert collection.classification.breaks[-1] == collection.statistics.max
    indices = [
        f.properties.class_index for f in collection.features if f.properties.value is not None
    ]
    assert all(0 <= index < collection.classification.classes for index in indices)  # type: ignore[operator]


async def test_geometria_de_visualizacao_e_menor_que_a_canonica(
    session: AsyncSession,
) -> None:
    """Prova no banco que a simplificação de ingestão realmente aconteceu."""
    await _require_ingested_data(session)

    rows = await session.execute(
        text(
            """
            SELECT lod::text AS lod, SUM(vertex_count) AS vertices
              FROM territory_geometries g
              JOIN territories t ON t.id = g.territory_id
             WHERE t.level = 'municipality'
             GROUP BY lod
            """
        )
    )
    vertices = {row.lod: int(row.vertices) for row in rows}

    assert vertices["overview"] < vertices["detail"] < vertices["canonical"]


async def test_bbox_do_escopo_dispensa_calculo_no_cliente(session: AsyncSession) -> None:
    await _require_ingested_data(session)

    collection = await map_service.get_map(session, level=TerritoryLevel.STATE)

    assert collection.bbox is not None
    west, south, east, north = collection.bbox
    assert west < east and south < north
    # Extensão continental do Brasil.
    assert -75 < west < -65
    assert 3 < north < 6


async def test_overview_lista_todos_os_indicadores_do_catalogo(
    session: AsyncSession,
) -> None:
    """Mesmo sem dado, o indicador aparece — a UI precisa mostrar "sem dado"."""
    await _require_ingested_data(session)

    overview = await territories_service.get_overview(session, "35")
    catalog = await indicators_service.list_indicators(session)

    assert overview.name == "São Paulo"
    assert overview.children_count == 645
    assert overview.capital is not None and overview.capital.ibge_code == "3550308"
    assert [i.key for i in overview.indicators] == [i.key for i in catalog.indicators]


async def test_overview_de_territorio_inexistente_resulta_em_404(
    session: AsyncSession,
) -> None:
    await _require_ingested_data(session)

    with pytest.raises(TerritoryNotFoundError):
        await territories_service.get_overview(session, "0000000")


async def test_series_historica_vem_ordenada_por_ano(session: AsyncSession) -> None:
    await _require_ingested_data(session)

    response = await territories_service.get_series(session, "35", indicator_key="gdp")

    assert len(response.series) == 1
    years = [point.year for point in response.series[0].points]
    assert years == sorted(years)
    assert len(years) > 10  # base pronta para gráficos temporais


async def test_pais_nao_aceita_parent(session: AsyncSession) -> None:
    """A raiz da hierarquia não tem pai.

    Antes, o filtro simplesmente não casava e a resposta saía 200 com zero
    features — um erro de uso virando silêncio.
    """
    await _require_ingested_data(session)

    with pytest.raises(InvalidParameterError):
        await map_service.get_map(session, level=TerritoryLevel.COUNTRY, parent_code="35")

    raiz = await map_service.get_map(session, level=TerritoryLevel.COUNTRY)
    assert raiz.scope.count == 1


def test_lod_publico_nao_expoe_a_geometria_canonica() -> None:
    """A malha canônica é verdade oficial, não payload de navegador.

    A malha municipal crua de Minas Gerais tem 8,8 MB; aceitá-la em um
    parâmetro de consulta anularia toda a estratégia de LOD.
    """
    publicos = {member.value for member in MapLod}

    assert publicos == {"overview", "detail"}
    assert GeometryLOD.CANONICAL.value not in publicos
    assert {lod.to_geometry_lod() for lod in MapLod} == {
        GeometryLOD.OVERVIEW,
        GeometryLOD.DETAIL,
    }


async def test_projecao_municipal_nao_vai_para_o_cache(session: AsyncSession) -> None:
    """Projeções grandes ficam fora do cache em processo.

    Uma coleção municipal desserializada ocupa ~7 MB: 40 entradas levaram a API
    de 92 MB para 378 MB de RSS, com taxa de acerto baixa porque cada usuário
    abre um estado diferente. As projeções compartilhadas continuam cacheadas.
    """
    await _require_ingested_data(session)

    municipal = await map_service.get_map(
        session,
        level=TerritoryLevel.MUNICIPALITY,
        parent_code="35",
        indicator_key="population",
    )
    assert municipal.scope.count > settings.read_cache_max_features
    assert map_service.cache_stats()["entries"] == 0

    await map_service.get_map(session, level=TerritoryLevel.STATE, indicator_key="population")
    assert map_service.cache_stats()["entries"] == 1


async def test_projecao_compartilhada_e_servida_do_cache(session: AsyncSession) -> None:
    await _require_ingested_data(session)

    first = await map_service.get_map(
        session, level=TerritoryLevel.STATE, indicator_key="population"
    )
    second = await map_service.get_map(
        session, level=TerritoryLevel.STATE, indicator_key="population"
    )

    # Mesmo objeto: a segunda chamada não tocou o banco.
    assert first is second
    assert map_service.cache_stats()["hits"] >= 1


async def test_projecao_com_etag_e_304(session: AsyncSession) -> None:
    await _require_ingested_data(session)
    async with _api(session) as client:
        res1 = await client.get("/api/v1/map?level=state")
        assert res1.status_code == 200
        etag = res1.headers.get("etag")
        assert etag is not None
        assert etag.startswith('W/"')

        res2 = await client.get("/api/v1/map?level=state", headers={"If-None-Match": etag})
        assert res2.status_code == 304
        assert res2.text == ""


async def test_etag_carrega_a_versao_dos_dados_e_304_nao_monta_a_projecao(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _require_ingested_data(session)
    async with _api(session) as client:
        first = await client.get("/api/v1/map?level=state&lod=overview")
        etag = first.headers["etag"]
        assert "stale-while-revalidate" in first.headers["cache-control"]

        async def unexpected(*_: object, **__: object) -> None:
            raise AssertionError("um 304 não pode montar a projeção")

        monkeypatch.setattr(map_service, "get_map", unexpected)
        cached = await client.get(
            "/api/v1/map?level=state&lod=overview", headers={"If-None-Match": etag}
        )
        assert cached.status_code == 304
        monkeypatch.undo()

        # Nova ingestão: o mesmo pedido deixa de casar com a versão anterior.
        async def next_version(_: AsyncSession) -> int:
            return 1

        monkeypatch.setattr(map_service, "data_version", next_version)
        fresh = await client.get(
            "/api/v1/map?level=state&lod=overview", headers={"If-None-Match": etag}
        )
        assert fresh.status_code == 200
        assert fresh.headers["etag"] != etag


async def test_valores_sem_geometria_coincidem_com_a_projecao(session: AsyncSession) -> None:
    await _require_ingested_data(session)
    full = await map_service.get_map(
        session, level=TerritoryLevel.MUNICIPALITY, parent_code="35", indicator_key="population"
    )
    values = await map_service.get_map_values(
        session, level=TerritoryLevel.MUNICIPALITY, parent_code="35", indicator_key="population"
    )
    assert values.indicator == full.indicator
    assert values.classification == full.classification
    assert values.statistics == full.statistics
    by_code = {item.ibge_code: item for item in values.values}
    assert len(by_code) == len(full.features)
    for feature in full.features:
        value = by_code[feature.properties.ibge_code]
        assert value.value == feature.properties.value
        assert value.class_index == feature.properties.class_index

    async with _api(session) as client:
        response = await client.get(
            "/api/v1/map/values?level=municipality&parent=35&indicator=population"
        )
        assert response.status_code == 200
        assert "geometry" not in response.text
        assert len(response.content) < 100_000
        missing = await client.get("/api/v1/map/values?level=state&indicator=nope")
        assert missing.status_code == 404
