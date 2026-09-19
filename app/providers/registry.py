"""Registro de providers por contexto.

Não é descoberta dinâmica nem plugin system — é uma lista declarativa. Cada
provider expõe o próprio `PROVIDER: ProviderDescriptor` (ver
`app/providers/ibge/__init__.py`), e este módulo só agrega. Adicionar uma
fonte nova é adicionar uma linha em `PROVIDERS`; nada mais aqui muda — pelo
mesmo espírito de `providers/ibge/datasets.py` para indicadores.

É este módulo que responde, para o endpoint `/contexts` ou qualquer chamador
interno futuro: quais providers existem, de qual contexto, e quais indicadores
cada um alimenta.
"""

from __future__ import annotations

from app.models import DataContext
from app.providers import ibge
from app.providers.descriptor import ProviderDescriptor

# Um provider por linha. `climate_environmental` e `biodiversity` ainda não
# têm nenhum: os contextos já existem (ver app/models/context.py) e já
# aparecem em `/contexts` com zero providers — prontos para receber o
# primeiro, sem migration nem mudança de contrato.
PROVIDERS: tuple[ProviderDescriptor, ...] = (ibge.PROVIDER,)


def providers_by_context(context: DataContext) -> tuple[ProviderDescriptor, ...]:
    return tuple(provider for provider in PROVIDERS if provider.context == context)


def provider_for_indicator(indicator_key: str) -> ProviderDescriptor | None:
    """A que provider pertence um indicador — útil para diagnóstico e para a UI."""
    for provider in PROVIDERS:
        if indicator_key in provider.provides:
            return provider
    return None


__all__ = ["PROVIDERS", "ProviderDescriptor", "provider_for_indicator", "providers_by_context"]
