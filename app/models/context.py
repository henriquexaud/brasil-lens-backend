"""Contexto de domínio dos dados.

Um indicador pertence a exatamente um contexto: o agrupamento temático que o
futuro frontend usará para alternar entre "camadas" de dados em vez de
carregar tudo de uma vez (ver docs/ARCHITECTURE.md). Adicionar um quarto
contexto é um valor novo aqui + uma migration — pelo mesmo motivo que um novo
`TerritoryLevel` não toca em `/map` ou `/views`.
"""

from __future__ import annotations

import enum


class DataContext(str, enum.Enum):
    """Agrupamento temático de indicadores."""

    # População, demografia, eleições, economia, setores econômicos e outros
    # indicadores territoriais. O contexto original do produto.
    SOCIOPOLITICAL = "sociopolitical"
    # Clima, chuva, vento, alertas, desastres naturais, relevo, correntes
    # marítimas. Suporta dados globais por natureza, mesmo com foco no Brasil.
    CLIMATE_ENVIRONMENTAL = "climate_environmental"
    # Espécies, plantas, animais, biomas e afins. Prioriza fontes brasileiras,
    # mas pode usar fontes globais quando fizer sentido.
    BIODIVERSITY = "biodiversity"


# Rótulo e descrição curtos de cada contexto, para o endpoint `/contexts`.
# Metadado estático (não fica no banco) porque descreve a capacidade do
# backend, não um dado ingerido.
DATA_CONTEXT_INFO: dict[DataContext, tuple[str, str]] = {
    DataContext.SOCIOPOLITICAL: (
        "Socioeconômico",
        "População, demografia, eleições, economia, setores econômicos e "
        "outros indicadores territoriais.",
    ),
    DataContext.CLIMATE_ENVIRONMENTAL: (
        "Clima e meio ambiente",
        "Clima, chuva, vento, alertas, desastres naturais, relevo e "
        "correntes marítimas — com suporte natural a dados globais.",
    ),
    DataContext.BIODIVERSITY: (
        "Biodiversidade",
        "Espécies, plantas, animais, biomas e informações relacionadas, com "
        "prioridade para dados brasileiros.",
    ),
}
