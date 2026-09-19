"""Contrato do endpoint de descoberta de contextos de dados.

É metadado sobre a **capacidade** do backend (quais contextos e providers
existem), não sobre dados ingeridos — por isso não consulta o banco. Ver
`app/services/contexts.py`.
"""

from __future__ import annotations

from app.schemas.common import CamelModel


class ContextProviderOut(CamelModel):
    key: str
    name: str
    homepage: str | None = None
    # Quantas chaves de indicador este provider declara fornecer — não é
    # "quantas têm dado no banco agora"; é a capacidade declarada no registro.
    indicator_count: int


class ContextOut(CamelModel):
    key: str
    name: str
    description: str
    providers: list[ContextProviderOut]
    indicator_count: int


class ContextListResponse(CamelModel):
    contexts: list[ContextOut]
