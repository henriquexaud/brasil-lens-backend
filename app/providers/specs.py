from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RatioIndicatorSpec:
    indicator_key: str
    numerator_key: str
    denominator_key: str
    dataset_name: str
    factor: Decimal = Decimal(1)
    notes: str = ""

    @property
    def dataset_code(self) -> str:
        return f"derived/{self.indicator_key}"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return (self.numerator_key, self.denominator_key)


@dataclass(frozen=True, slots=True)
class GrowthIndicatorSpec:
    indicator_key: str
    base_key: str
    dataset_name: str
    factor: Decimal = Decimal(100)
    notes: str = ""

    @property
    def dataset_code(self) -> str:
        return f"derived/{self.indicator_key}"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return (self.base_key,)


@dataclass(frozen=True, slots=True)
class ShareIndicatorSpec:
    indicator_key: str
    base_key: str
    dataset_name: str
    factor: Decimal = Decimal(100)
    notes: str = ""

    @property
    def dataset_code(self) -> str:
        return f"derived/{self.indicator_key}"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return (self.base_key,)


DerivedIndicatorSpec = RatioIndicatorSpec | GrowthIndicatorSpec | ShareIndicatorSpec

__all__ = [
    "DerivedIndicatorSpec",
    "GrowthIndicatorSpec",
    "RatioIndicatorSpec",
    "ShareIndicatorSpec",
]
