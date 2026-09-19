"""Provider CEMADEN: pluviômetros automáticos.

Ver `app/providers/cemaden/rain_gauges.py` para o porquê deste provider ainda
não fala com uma fonte real — é risco conhecido e documentado, não uma
implementação esquecida.
"""

from __future__ import annotations

from app.models import DataContext
from app.providers.descriptor import ProviderDescriptor

PROVIDER_RAIN_GAUGES = ProviderDescriptor(
    key="cemaden_rain_gauges",
    name="CEMADEN — Pluviômetros Automáticos",
    context=DataContext.CLIMATE_ENVIRONMENTAL,
    provides=(),
    homepage="https://mapainterativo.cemaden.gov.br/",
)

__all__ = ["PROVIDER_RAIN_GAUGES"]
