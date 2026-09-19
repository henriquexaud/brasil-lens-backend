"""Provider INMET: estações automáticas + alertas meteorológicos oficiais.

Alimenta o contexto `climate_environmental`, mas não fornece nenhum
`indicator.key` — estações e alertas não são coropletas, então `provides`
fica vazio de propósito (ver `app/providers/registry.py`).
"""

from __future__ import annotations

from app.models import DataContext
from app.providers.descriptor import ProviderDescriptor

PROVIDER_STATIONS = ProviderDescriptor(
    key="inmet_stations",
    name="INMET — Estações Meteorológicas Automáticas",
    context=DataContext.CLIMATE_ENVIRONMENTAL,
    provides=(),
    homepage="https://portal.inmet.gov.br/servicos/esta%C3%A7%C3%B5es-autom%C3%A1ticas",
)

PROVIDER_ALERTS = ProviderDescriptor(
    key="inmet_alerts",
    name="INMET — Avisos Meteorológicos",
    context=DataContext.CLIMATE_ENVIRONMENTAL,
    provides=(),
    homepage="https://alertas2.inmet.gov.br/",
)

__all__ = ["PROVIDER_ALERTS", "PROVIDER_STATIONS"]
