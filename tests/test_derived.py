"""Indicadores derivados: fórmula, unidades e regra temporal.

Os testes mais valiosos aqui comparam o que derivamos com o que o **IBGE
publica**: densidade (variável 614 da tabela 4714), taxa de urbanização
(variável 1000093 da tabela 9923), participação no PIB nacional (variável 496
da tabela 5938) e o crescimento anual entre os censos de 2010 e 2022 (0,52%
a.a., divulgado pelo próprio instituto). Se uma fórmula, uma unidade ou um
multiplicador estiverem errados, o teste falha contra um número oficial — e não
contra um valor que nós mesmos inventamos.
"""

from decimal import Decimal

import pytest

from app.providers.ibge.datasets import DERIVED_INDICATORS, SOURCED_INDICATORS
from app.services.derived import expected_growth, expected_share, expected_value
from tests.conftest import load_fixture


def _values_by_state() -> dict[str, dict[str, Decimal]]:
    payload = load_fixture("agregados_4714_uf.json")
    result: dict[str, dict[str, Decimal]] = {}
    for variable in payload:
        for series in variable["resultados"][0]["series"]:
            code = series["localidade"]["id"]
            value = next(iter(series["serie"].values()))
            result.setdefault(code, {})[variable["id"]] = Decimal(value)
    return result


def test_densidade_derivada_bate_com_a_publicada_pelo_ibge() -> None:
    for code, variables in _values_by_state().items():
        population = variables["93"]
        area = variables["6318"]
        published = variables["614"]  # densidade publicada, 2 casas decimais

        derived = expected_value(population, area)

        assert abs(derived - published) < Decimal(
            "0.01"
        ), f"UF {code}: derivado {derived} vs. publicado {published}"


def test_pib_per_capita_usa_pib_ja_normalizado_em_reais() -> None:
    """PIB vem em Mil Reais da fonte; per capita só faz sentido após converter."""
    pib_mil_reais = Decimal("2_700_000_000")  # como a fonte publica
    populacao = Decimal("44_411_238")

    correto = expected_value(pib_mil_reais * 1000, populacao)
    esquecendo_multiplicador = expected_value(pib_mil_reais, populacao)

    # A comparação é aproximada porque o resultado é quantizado em 6 casas
    # antes da multiplicação — o ponto do teste é a ordem de grandeza de 1000x.
    assert abs(correto - esquecendo_multiplicador * 1000) < Decimal("0.01")
    # Ordem de grandeza plausível de PIB per capita em reais.
    assert Decimal("10000") < correto < Decimal("200000")
    assert esquecendo_multiplicador < Decimal("1000")


def test_divisao_produz_seis_casas_decimais_como_a_coluna() -> None:
    value = expected_value(Decimal(1), Decimal(3))

    assert value == Decimal("0.333333")
    assert value.as_tuple().exponent == -6


def test_registro_de_derivados_e_coerente_com_o_catalogo() -> None:
    """Uma dependência faltando aqui é erro de configuração, não de runtime."""
    sourced = {spec.indicator_key for spec in SOURCED_INDICATORS}
    derived = {spec.indicator_key for spec in DERIVED_INDICATORS}

    for spec in DERIVED_INDICATORS:
        for dependency in spec.dependencies:
            assert dependency in sourced | derived
        assert (
            spec.indicator_key not in sourced
        ), f"'{spec.indicator_key}' não pode ser importado e derivado ao mesmo tempo"


def test_pib_per_capita_e_derivado_porque_a_fonte_nao_publica() -> None:
    """Verificado contra os metadados da tabela 5938: nenhuma variável per capita."""
    keys = {spec.indicator_key for spec in DERIVED_INDICATORS}

    assert "gdp_per_capita" in keys
    assert "population_density" in keys


# ---------------------------------------------------------------------------
# Urbanização, participação no PIB e crescimento.
#
# Os três são conferidos contra números que o próprio IBGE publica — que é o
# único jeito de um teste de fórmula não se limitar a repetir a fórmula.
# ---------------------------------------------------------------------------


def _by_code(payload: list[dict], variable_id: str, category: str | None = None) -> dict[str, str]:
    """Valores de uma variável (opcionalmente de uma categoria) por território."""
    variable = next(item for item in payload if item["id"] == variable_id)
    values: dict[str, str] = {}
    for result in variable["resultados"]:
        if category is not None:
            categories = result["classificacoes"][0]["categoria"]
            if category not in categories:
                continue
        for series in result["series"]:
            values[series["localidade"]["id"]] = next(iter(series["serie"].values()))
    return values


def test_urbanizacao_derivada_bate_com_o_percentual_publicado_pelo_ibge() -> None:
    """A taxa derivada é conferida contra a variável 1000093 da tabela 9923."""
    payload = load_fixture("agregados_9923_situacao_domicilio.json")
    urbana = _by_code(payload, "93", category="1")
    total = _by_code(payload, "93", category="6795")
    publicado = _by_code(payload, "1000093", category="1")

    for code, published in publicado.items():
        derived = expected_value(Decimal(urbana[code]), Decimal(total[code]), Decimal(100))

        assert abs(derived - Decimal(published)) < Decimal(
            "0.01"
        ), f"{code}: derivado {derived} vs. publicado {published}"

    # E o número nacional, que é o mais citado: 87,4% em 2022.
    assert Decimal(publicado["1"]) == Decimal("87.41")


def test_participacao_no_pib_bate_com_a_publicada_pelo_ibge() -> None:
    """Conferido contra a variável 496 da tabela 5938 (participação no Brasil)."""
    payload = load_fixture("agregados_5938_setores.json")
    pib = _by_code(payload, "37")
    publicado = _by_code(payload, "496")
    nacional = Decimal(pib["1"])  # N1 = Brasil

    for code, published in publicado.items():
        derived = expected_share(Decimal(pib[code]), nacional)

        assert abs(derived - Decimal(published)) < Decimal(
            "0.01"
        ), f"{code}: derivado {derived} vs. publicado {published}"

    # O Brasil participa de si mesmo com 100% — a definição fecha.
    assert expected_share(nacional, nacional) == Decimal("100.000000")


def test_participacao_independe_do_multiplicador_da_fonte() -> None:
    """Razão entre dois valores da mesma fonte: a unidade se cancela."""
    payload = load_fixture("agregados_5938_setores.json")
    pib = _by_code(payload, "37")

    em_mil_reais = expected_share(Decimal(pib["35"]), Decimal(pib["1"]))
    em_reais = expected_share(Decimal(pib["35"]) * 1000, Decimal(pib["1"]) * 1000)

    assert em_mil_reais == em_reais


def test_crescimento_anualizado_reproduz_a_taxa_censitaria_publicada() -> None:
    """Censo 2010 → Censo 2022: o IBGE divulgou 0,52% ao ano.

    São 12 anos entre os dois censos. Uma variação simples devolveria 6,46% —
    o crescimento do período inteiro rotulado como se fosse de um ano.
    """
    censo_2010 = Decimal("190755799")
    censo_2022 = Decimal("203080756")

    anualizado = expected_growth(censo_2022, censo_2010, years=12)
    simples = expected_growth(censo_2022, censo_2010, years=1)

    assert abs(anualizado - Decimal("0.52")) < Decimal("0.01")
    assert simples > Decimal("6")


def test_crescimento_anualizado_e_consistente_quando_composto() -> None:
    """Aplicar a taxa Δ vezes tem de reconstruir a razão original."""
    inicial = Decimal("1000000")
    final = Decimal("1150000")

    taxa = expected_growth(final, inicial, years=3) / 100
    reconstruido = inicial * (1 + taxa) ** 3

    assert abs(reconstruido - final) < Decimal("1")


def test_crescimento_negativo_e_preservado() -> None:
    """População que encolhe precisa aparecer como taxa negativa, não como zero."""
    assert expected_growth(Decimal("990"), Decimal("1000"), years=1) == Decimal("-1.000000")


def test_crescimento_exige_intervalo_positivo() -> None:
    """Sem intervalo não existe taxa anual — falha alto em vez de dividir por zero."""
    with pytest.raises(ValueError):
        expected_growth(Decimal("100"), Decimal("100"), years=0)
