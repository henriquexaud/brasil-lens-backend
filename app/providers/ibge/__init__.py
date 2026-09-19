"""Provider IBGE: hierarquia territorial (Localidades), malhas e agregados
(SIDRA v3). Alimenta o contexto sociopolítico.
"""

from __future__ import annotations

from app.models import DataContext
from app.providers.descriptor import ProviderDescriptor
from app.providers.ibge.datasets import DERIVED_INDICATORS, SOURCED_INDICATORS

PROVIDER = ProviderDescriptor(
    key="ibge",
    name="IBGE — Instituto Brasileiro de Geografia e Estatística",
    context=DataContext.SOCIOPOLITICAL,
    provides=tuple(
        {
            *(spec.indicator_key for spec in SOURCED_INDICATORS),
            *(spec.indicator_key for spec in DERIVED_INDICATORS),
        }
    ),
    homepage="https://servicodados.ibge.gov.br/api/docs",
)

__all__ = ["PROVIDER"]
