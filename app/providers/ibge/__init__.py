"""Provider IBGE: hierarquia territorial (Localidades), malhas e agregados
(SIDRA v3). Alimenta o contexto sociopolítico.
"""

from __future__ import annotations

from app.models import DataContext
from app.providers.descriptor import ProviderDescriptor
from app.providers.ibge.datasets import DERIVED_INDICATORS, SOURCED_INDICATORS

# Mesma fonte (IBGE), dois descritores: o registro conta indicadores por
# contexto, e um indicador (disaster_affected_people) pertence ao contexto
# climático mesmo vindo do mesmo pacote/HTTP client que os sociopolíticos.
_CLIMATE_KEYS = frozenset({"disaster_affected_people"})

PROVIDER = ProviderDescriptor(
    key="ibge",
    name="IBGE — Instituto Brasileiro de Geografia e Estatística",
    context=DataContext.SOCIOPOLITICAL,
    provides=tuple(
        {
            *(spec.indicator_key for spec in SOURCED_INDICATORS),
            *(spec.indicator_key for spec in DERIVED_INDICATORS),
        }
        - _CLIMATE_KEYS
    ),
    homepage="https://servicodados.ibge.gov.br/api/docs",
)

PROVIDER_CLIMATE = ProviderDescriptor(
    key="ibge_climate",
    name="IBGE — Instituto Brasileiro de Geografia e Estatística",
    context=DataContext.CLIMATE_ENVIRONMENTAL,
    provides=tuple(_CLIMATE_KEYS),
    homepage="https://servicodados.ibge.gov.br/api/docs",
)

__all__ = ["PROVIDER", "PROVIDER_CLIMATE"]
