"""Estatísticas e classificação dos valores de um mapa.

Funções puras: nenhuma dependência de banco, HTTP ou Pydantic. É de propósito —
esta é a parte do sistema com mais casos de borda (valores iguais, poucos
valores distintos, escopo sem dado algum) e precisa ser testável isoladamente.

Divisão de responsabilidade com o frontend: **a API decide valores, intervalos e
normalização; o frontend decide cores, tema e apresentação.** Por isso aqui não
existe paleta, hex ou nome de cor.

Por que quantis e não intervalos iguais: PIB per capita e densidade demográfica
são fortemente assimétricos no Brasil. Com intervalos iguais, praticamente todos
os territórios cairiam na primeira classe e o mapa ficaria de uma cor só.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

DEFAULT_CLASS_COUNT = 5
ClassificationMethod = Literal["quantile"]

# Mesma escala da coluna `indicator_values.value`. Sem isto, a média sai com
# a precisão bruta da divisão de Decimal (dezenas de casas) e as quebras
# ficam ilegíveis na legenda.
_SCALE = Decimal("0.000001")


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_SCALE)


@dataclass(frozen=True, slots=True)
class Statistics:
    min: Decimal
    max: Decimal
    mean: Decimal
    median: Decimal
    count: int
    missing: int


@dataclass(frozen=True, slots=True)
class Classification:
    method: ClassificationMethod
    classes: int
    # Limite superior de cada classe; `breaks[-1]` é sempre o máximo observado.
    breaks: list[Decimal]


@dataclass(frozen=True, slots=True)
class ValueDistribution:
    """Resultado completo: `None` em ambos quando o escopo não tem valor algum."""

    statistics: Statistics | None
    classification: Classification | None

    def normalize(self, value: Decimal | None) -> float | None:
        """Posição do valor entre mínimo e máximo, em [0, 1].

        Não é persistido: depende do indicador, do ano e do conjunto de
        territórios consultado. Serve para escalas contínuas (opacidade,
        interpolação) — a coropleta em si usa `class_index`.
        """
        if value is None or self.statistics is None:
            return None
        spread = self.statistics.max - self.statistics.min
        if spread == 0:
            # Todos os valores iguais: qualquer normalização é arbitrária.
            return 0.0
        return round(float((value - self.statistics.min) / spread), 6)

    def class_index(self, value: Decimal | None) -> int | None:
        """Índice da classe (0-based) a que o valor pertence."""
        if value is None or self.classification is None:
            return None
        breaks = self.classification.breaks
        index = bisect_left(breaks, value)
        return min(index, len(breaks) - 1)


def _percentile(sorted_values: Sequence[Decimal], fraction: float) -> Decimal:
    """Percentil com interpolação linear (equivalente a `percentile_cont`)."""
    if not sorted_values:
        raise ValueError("sequência vazia")
    if len(sorted_values) == 1:
        return sorted_values[0]

    position = Decimal(str(fraction)) * (len(sorted_values) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    weight = position - lower_index
    lower = sorted_values[lower_index]
    upper = sorted_values[upper_index]
    return lower + (upper - lower) * weight


def describe(
    values: Sequence[Decimal | None],
    *,
    classes: int = DEFAULT_CLASS_COUNT,
) -> ValueDistribution:
    """Calcula estatísticas e quebras por quantil sobre os valores presentes.

    Valores ausentes são contados em `missing` e ignorados no resto: território
    sem dado não pode puxar mínimo, média ou classe para baixo.
    """
    present = sorted(value for value in values if value is not None)
    missing = len(values) - len(present)

    if not present:
        return ValueDistribution(statistics=None, classification=None)

    statistics = Statistics(
        min=_quantize(present[0]),
        max=_quantize(present[-1]),
        mean=_quantize(sum(present, Decimal(0)) / len(present)),
        median=_quantize(_percentile(present, 0.5)),
        count=len(present),
        missing=missing,
    )

    requested = max(1, classes)
    raw_breaks = [
        _quantize(_percentile(present, (index + 1) / requested)) for index in range(requested)
    ]

    # Quebras duplicadas acontecem quando há menos valores distintos que classes
    # (ex.: 3 estados com dado e 5 classes). Manter duplicatas produziria classes
    # vazias e uma legenda enganosa.
    breaks: list[Decimal] = []
    for candidate in raw_breaks:
        if not breaks or candidate > breaks[-1]:
            breaks.append(candidate)
    if breaks[-1] != statistics.max:
        breaks[-1] = statistics.max

    return ValueDistribution(
        statistics=statistics,
        classification=Classification(method="quantile", classes=len(breaks), breaks=breaks),
    )
