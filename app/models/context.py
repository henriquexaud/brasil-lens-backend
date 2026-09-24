"""Contexto de domínio dos dados.

Um indicador pertence a exatamente um contexto: o agrupamento temático que o
frontend usa para alternar entre "camadas" de dados.
"""

from __future__ import annotations

import enum


class DataContext(str, enum.Enum):
    """Agrupamento temático de indicadores."""

    # População, demografia, economia, setores econômicos e outros
    # indicadores territoriais.
    SOCIOPOLITICAL = "sociopolitical"
    # Clima, chuva, vento, alertas, desastres naturais, relevo, correntes
    # marítimas.
    CLIMATE_ENVIRONMENTAL = "climate_environmental"


# Rótulo e descrição curtos de cada contexto, para o endpoint `/contexts`.
DATA_CONTEXT_INFO: dict[DataContext, tuple[str, str]] = {
    DataContext.SOCIOPOLITICAL: (
        "Socioeconômico",
        "População, demografia, economia, setores econômicos e "
        "outros indicadores territoriais.",
    ),
    DataContext.CLIMATE_ENVIRONMENTAL: (
        "Clima e meio ambiente",
        "Clima, chuva, vento, alertas, desastres naturais, relevo e "
        "correntes marítimas.",
    ),
}
