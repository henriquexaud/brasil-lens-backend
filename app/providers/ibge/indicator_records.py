from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class IndicatorObservation:
    ibge_code: str
    reference_year: int
    value: Decimal
