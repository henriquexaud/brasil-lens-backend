"""Normalização do provider de Agregados (SIDRA v3).

Cobre as três conversões que o resto do sistema depende de estarem corretas:
identificação de território, descarte de sentinelas e multiplicador de unidade.
"""

from decimal import Decimal

from app.providers.ibge.agregados import (
    AggregateQuery,
    parse_observations,
    parse_value,
    reference_year,
    territory_code,
)
from tests.conftest import load_fixture


def test_colisao_de_id_entre_niveis_e_resolvida_pelo_nivel() -> None:
    """Caso real e silenciosamente destrutivo.

    No nível N1 o Brasil vem com `localidade.id = "1"` — exatamente o id da
    região Norte em N2. Mapear o id sem olhar o nível atribuiria a população do
    Brasil (203 milhões) à região Norte (17 milhões).
    """
    payload = load_fixture("agregados_4714_n1_n2.json")
    observations, _ = parse_observations(payload)
    by_code = {observation.ibge_code: observation.value for observation in observations}

    assert by_code["BR"] == Decimal("203080756")
    assert by_code["1"] == Decimal("17354884")  # Norte, não Brasil
    assert by_code["BR"] != by_code["1"]


def test_apenas_niveis_aceitos_sao_convertidos() -> None:
    payload = load_fixture("agregados_4714_n1_n2.json")

    observations, _ = parse_observations(payload, accept_levels=frozenset({"N2"}))

    codes = {observation.ibge_code for observation in observations}
    assert codes == {"1", "2", "3", "4", "5"}
    assert "BR" not in codes


def test_multiplicador_normaliza_unidade_da_fonte() -> None:
    """A tabela 5938 publica PIB em Mil Reais; o indicador é em reais."""
    payload = load_fixture("agregados_5938_uf.json")

    sem_multiplicador, _ = parse_observations(payload)
    com_multiplicador, _ = parse_observations(payload, multiplier=Decimal(1000))

    base = {(o.ibge_code, o.reference_year): o.value for o in sem_multiplicador}
    normalizado = {(o.ibge_code, o.reference_year): o.value for o in com_multiplicador}
    chave = ("35", 2023)

    assert normalizado[chave] == base[chave] * 1000


def test_series_com_varios_anos_geram_uma_observacao_por_ano() -> None:
    payload = load_fixture("agregados_5938_uf.json")

    observations, _ = parse_observations(payload)

    anos_sp = sorted(o.reference_year for o in observations if o.ibge_code == "35")
    assert anos_sp == [2021, 2022, 2023]


def test_sentinelas_de_indisponibilidade_sao_descartadas() -> None:
    """Sentinela não pode virar zero: zero é um valor, ausência não é."""
    for token in ("...", "..", "-", "X", "", "*"):
        assert parse_value(token, Decimal(1)) is None

    assert parse_value("1234", Decimal(1)) == Decimal("1234")
    assert parse_value(" 12.5 ", Decimal(1)) == Decimal("12.5")
    assert parse_value("texto inesperado", Decimal(1)) is None


def test_descartes_sao_contados_para_ficarem_visiveis() -> None:
    payload = [
        {
            "id": "93",
            "variavel": "População residente",
            "unidade": "Pessoas",
            "resultados": [
                {
                    "series": [
                        {
                            "localidade": {
                                "id": "35",
                                "nome": "São Paulo",
                                "nivel": {"id": "N3", "nome": "Unidade da Federação"},
                            },
                            "serie": {"2022": "100", "2021": "...", "2020": "-"},
                        }
                    ]
                }
            ],
        }
    ]

    observations, discarded = parse_observations(payload)

    assert len(observations) == 1
    # "veio pouco dado" precisa ser mensurável, não silencioso.
    assert discarded == 2


def test_traducao_de_codigo_territorial() -> None:
    assert territory_code("N1", "1") == "BR"
    assert territory_code("N2", "3") == "3"
    assert territory_code("N3", "35") == "35"
    assert territory_code("N6", "3550308") == "3550308"


# ---------------------------------------------------------------------------
# Periodicidades e variáveis compostas.
#
# O destino (`indicator_values`) tem chave `(território, indicador, ano)`. Toda
# fonte precisa chegar nesse formato, e é aqui que isso acontece.
# ---------------------------------------------------------------------------


def _serie(payload: list[dict], variable_id: str, code: str) -> dict[str, str]:
    variable = next(item for item in payload if item["id"] == variable_id)
    series = next(
        item
        for result in variable["resultados"]
        for item in result["series"]
        if item["localidade"]["id"] == code
    )
    return series["serie"]


def test_serie_trimestral_vira_media_anual() -> None:
    """Tabela 6468 publica 4 trimestres por ano; o indicador é anual."""
    payload = load_fixture("agregados_6468_desocupacao_trimestral.json")
    trimestres = _serie(payload, "4099", "1")  # Brasil

    observations, _ = parse_observations(payload, accept_levels=frozenset({"N1"}))
    by_year = {o.reference_year: o.value for o in observations}

    esperado_2024 = (
        sum(
            (Decimal(trimestres[period]) for period in ("202401", "202402", "202403", "202404")),
            Decimal(0),
        )
        / 4
    )
    assert by_year[2024] == esperado_2024.quantize(Decimal("0.000001"))


def test_ano_incompleto_usa_os_trimestres_publicados() -> None:
    """2026 tem só 2 trimestres divulgados: o dado aparece, não some."""
    payload = load_fixture("agregados_6468_desocupacao_trimestral.json")
    trimestres = _serie(payload, "4099", "1")

    observations, _ = parse_observations(payload, accept_levels=frozenset({"N1"}))
    by_year = {o.reference_year: o.value for o in observations}

    publicados = [Decimal(trimestres[p]) for p in ("202601", "202602")]
    assert by_year[2026] == (sum(publicados, Decimal(0)) / 2).quantize(Decimal("0.000001"))


def test_nenhuma_duplicata_de_territorio_e_ano() -> None:
    """Se duas observações tivessem a mesma chave, o upsert falharia no banco.

    O PostgreSQL recusa `ON CONFLICT DO UPDATE` que afete a mesma linha duas
    vezes — seria erro de ingestão, não valor errado, e só apareceria contra o
    banco. A garantia mora aqui.
    """
    payload = load_fixture("agregados_6468_desocupacao_trimestral.json")

    observations, _ = parse_observations(payload)

    chaves = [(o.ibge_code, o.reference_year) for o in observations]
    assert len(chaves) == len(set(chaves))


def test_serie_anual_atravessa_sem_alteracao() -> None:
    """A redução para ano não pode mexer em quem já é anual."""
    payload = load_fixture("agregados_7395_renda_domiciliar.json")
    serie = _serie(payload, "4196", "1")

    observations, _ = parse_observations(payload, accept_levels=frozenset({"N1"}))
    by_year = {o.reference_year: o.value for o in observations}

    assert by_year[2024] == Decimal(serie["2024"])
    assert by_year[2025] == Decimal(serie["2025"])


def test_variaveis_da_mesma_consulta_sao_somadas() -> None:
    """Serviços = serviços privados (6575) + administração pública (525)."""
    payload = load_fixture("agregados_5938_setores.json")
    servicos = [item for item in payload if item["id"] in {"6575", "525"}]

    observations, _ = parse_observations(
        servicos,
        multiplier=Decimal(1000),
        accept_levels=frozenset({"N3"}),
    )
    by_code = {o.ibge_code: o.value for o in observations}

    privados = Decimal(_serie(payload, "6575", "35")["2021"])
    publica = Decimal(_serie(payload, "525", "35")["2021"])
    assert by_code["35"] == (privados + publica) * 1000
    # E o ponto do indicador: só a parcela privada subestimaria o setor.
    assert by_code["35"] > privados * 1000


def test_tres_setores_somam_o_valor_adicionado_publicado_pelo_ibge() -> None:
    """Confere a escolha das variáveis contra um número da própria fonte.

    Se `gdp_services` usasse só a variável 6575, a soma dos três setores não
    fecharia com o VAB total (variável 498) que o IBGE publica.
    """
    payload = load_fixture("agregados_5938_setores.json")

    for series in next(item for item in payload if item["id"] == "498")["resultados"][0]["series"]:
        code = series["localidade"]["id"]
        total = Decimal(series["serie"]["2021"])
        setores = sum(
            (
                Decimal(_serie(payload, variable, code)["2021"])
                for variable in ("513", "517", "6575", "525")
            ),
            Decimal(0),
        )
        # Diferença de arredondamento da fonte, em milhares de reais.
        assert abs(setores - total) <= Decimal(1), f"{code}: setores {setores} vs. VAB {total}"


def test_soma_parcial_e_descartada_em_vez_de_virar_valor_menor() -> None:
    """Com uma variável indisponível, a soma some — não vira um número menor.

    Um "serviços" sem a administração pública tem exatamente a mesma cara de um
    valor completo, e é o tipo de erro que ninguém percebe no mapa.
    """
    payload = [
        {
            "id": "6575",
            "variavel": "VAB serviços",
            "unidade": "Mil Reais",
            "resultados": [
                {
                    "series": [
                        {
                            "localidade": {
                                "id": "35",
                                "nome": "São Paulo",
                                "nivel": {"id": "N3", "nome": "Unidade da Federação"},
                            },
                            "serie": {"2023": "100"},
                        }
                    ]
                }
            ],
        },
        {
            "id": "525",
            "variavel": "VAB administração pública",
            "unidade": "Mil Reais",
            "resultados": [
                {
                    "series": [
                        {
                            "localidade": {
                                "id": "35",
                                "nome": "São Paulo",
                                "nivel": {"id": "N3", "nome": "Unidade da Federação"},
                            },
                            "serie": {"2023": "..."},
                        }
                    ]
                }
            ],
        },
    ]

    observations, discarded = parse_observations(payload)

    assert observations == []
    assert discarded == 2  # a sentinela e a parcela que ficaria órfã


def test_classificacao_entra_nos_parametros_da_consulta() -> None:
    """População urbana não é variável: é categoria da variável 93."""
    urbana = AggregateQuery(table="9923", variable="93", classification="1[1]")
    total = AggregateQuery(table="9923", variable="93")

    assert urbana.params == {"classificacao": "1[1]"}
    assert total.params == {}


def test_ano_de_referencia_cobre_os_formatos_de_periodo() -> None:
    assert reference_year("2022") == 2022
    assert reference_year("202203") == 2022  # trimestral/mensal
    assert reference_year("sem-ano") is None
