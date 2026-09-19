"""Contratos de indicador derivado, compartilhados entre providers.

Três formas de derivação — razão, crescimento, participação — usadas por
`services/derived.py`. Ficam aqui, fora de `providers/ibge/`, porque não
dependem de nenhuma fonte específica: operam inteiramente sobre
`indicator_values`, resolvendo `numerator_key`/`denominator_key`/`base_key`
contra o catálogo de indicadores, nunca contra uma API externa. Qualquer
provider — IBGE hoje, outro amanhã — pode declarar indicadores derivados a
partir dos seus próprios indicadores usando estes mesmos tipos, sem duplicá-los
nem importar de dentro do pacote de outro provider.

Isolar isto de `providers/ibge/datasets.py` é o que permite a
`services/derived.py` (regra de negócio genérica) depender só deste contrato,
em vez de depender do pacote de uma fonte específica.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RatioIndicatorSpec:
    """Razão entre dois indicadores no mesmo território (A ÷ B × fator)."""

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
    """Variação anual de um indicador em relação ao ano anterior com dado.

    A taxa é **geométrica anualizada**: séries reais têm lacunas, e uma
    diferença simples entre anos não consecutivos devolveria o crescimento de
    vários anos rotulado como se fosse de um.
    """

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
    """Participação do território no total nacional do mesmo indicador e ano."""

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
