"""Classificação e estatísticas — a parte com mais casos de borda do sistema."""

from decimal import Decimal

from app.services.classification import describe


def _decimals(*values: float) -> list[Decimal]:
    return [Decimal(str(value)) for value in values]


def test_statistics_ignoram_ausencias() -> None:
    distribution = describe([*_decimals(10, 20, 30), None])

    assert distribution.statistics is not None
    assert distribution.statistics.min == Decimal("10")
    assert distribution.statistics.max == Decimal("30")
    assert distribution.statistics.mean == Decimal("20")
    assert distribution.statistics.median == Decimal("20")
    assert distribution.statistics.count == 3
    # Território sem dado é contado, mas não entra em mínimo, média nem classe.
    assert distribution.statistics.missing == 1


def test_escopo_sem_nenhum_valor_nao_inventa_estatistica() -> None:
    distribution = describe([None, None])

    assert distribution.statistics is None
    assert distribution.classification is None
    assert distribution.normalize(None) is None
    assert distribution.class_index(Decimal("5")) is None


def test_normalizacao_mapeia_extremos_em_zero_e_um() -> None:
    distribution = describe(_decimals(100, 200, 300))

    assert distribution.normalize(Decimal("100")) == 0.0
    assert distribution.normalize(Decimal("300")) == 1.0
    assert distribution.normalize(Decimal("200")) == 0.5
    assert distribution.normalize(None) is None


def test_valores_todos_iguais_nao_dividem_por_zero() -> None:
    distribution = describe(_decimals(7, 7, 7))

    assert distribution.normalize(Decimal("7")) == 0.0
    assert distribution.classification is not None
    # Uma classe só: manter 5 quebras idênticas produziria legenda enganosa.
    assert distribution.classification.classes == 1
    assert distribution.class_index(Decimal("7")) == 0


def test_quebras_sao_deduplicadas_quando_ha_poucos_valores() -> None:
    distribution = describe(_decimals(1, 1, 1, 9), classes=5)

    assert distribution.classification is not None
    breaks = distribution.classification.breaks
    assert breaks == sorted(set(breaks))
    assert breaks[-1] == Decimal("9")


def test_ultima_quebra_e_sempre_o_maximo() -> None:
    distribution = describe(_decimals(*range(1, 101)), classes=5)

    assert distribution.classification is not None
    assert distribution.classification.breaks[-1] == Decimal("100")
    assert distribution.classification.classes == 5


def test_classes_cobrem_todos_os_valores() -> None:
    values = _decimals(5, 12, 19, 31, 44, 58, 77, 91)
    distribution = describe(values, classes=4)

    assert distribution.classification is not None
    indices = [distribution.class_index(value) for value in values]
    assert all(index is not None for index in indices)
    assert min(indices) == 0  # type: ignore[type-var]
    assert max(indices) == distribution.classification.classes - 1  # type: ignore[operator]


def test_distribuicao_assimetrica_nao_colapsa_em_uma_classe() -> None:
    """O caso que motivou usar quantis: PIB per capita é muito assimétrico.

    Com intervalos iguais, quase todos os territórios cairiam na primeira
    classe e o mapa ficaria de uma cor só.
    """
    values = _decimals(20_000, 21_000, 22_000, 23_000, 24_000, 25_000, 400_000)
    distribution = describe(values, classes=5)

    assert distribution.classification is not None
    indices = {distribution.class_index(value) for value in values}
    assert len(indices) >= 4
