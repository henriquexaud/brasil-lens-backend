from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

DEFAULT_CLASS_COUNT = 10
ClassificationMethod = Literal["quantile"]


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

    breaks: list[Decimal]


@dataclass(frozen=True, slots=True)
class ValueDistribution:
    statistics: Statistics | None
    classification: Classification | None

    def normalize(self, value: Decimal | None) -> float | None:
        if value is None or self.statistics is None:
            return None
        spread = self.statistics.max - self.statistics.min
        if spread == 0:
            return 0.0
        return round(float((value - self.statistics.min) / spread), 6)

    def class_index(self, value: Decimal | None) -> int | None:
        if value is None or self.classification is None:
            return None
        breaks = self.classification.breaks
        index = bisect_left(breaks, value)
        return min(index, len(breaks) - 1)


def _percentile(sorted_values: Sequence[Decimal], fraction: float) -> Decimal:
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
    breaks = [
        _quantize(_percentile(present, (index + 1) / requested)) for index in range(requested)
    ]

    breaks[-1] = statistics.max

    return ValueDistribution(
        statistics=statistics,
        classification=Classification(method="quantile", classes=len(breaks), breaks=breaks),
    )
