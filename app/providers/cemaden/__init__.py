"""Provider CEMADEN: alertas de risco geo-hidrológico.

Complementar ao INMET (`app/providers/inmet/`): o INMET avisa sobre o
fenômeno meteorológico (chuva intensa, tempestade, vento, onda de calor); o
CEMADEN avisa sobre o risco/impacto associado (inundação, enxurrada,
alagamento, deslizamento). Os dois produzem o mesmo `WeatherAlertRecord` e
convivem na mesma tabela `weather_alerts` sem se fundir — um aviso de chuva
do INMET e um risco de deslizamento do CEMADEN na mesma cidade são dois
alertas distintos, não um só.

Assim como `inmet.PROVIDER_ALERTS`, não fornece nenhum `indicator.key`.
"""

from __future__ import annotations

from app.models import DataContext
from app.providers.descriptor import ProviderDescriptor

PROVIDER_ALERTS = ProviderDescriptor(
    key="cemaden_alerts",
    name="CEMADEN — Alertas de Risco Geo-Hidrológico",
    context=DataContext.CLIMATE_ENVIRONMENTAL,
    provides=(),
    homepage="https://www.gov.br/cemaden/pt-br",
)

__all__ = ["PROVIDER_ALERTS"]
