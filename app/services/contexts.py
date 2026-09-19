"""Caso de uso do catálogo de contextos.

Ao contrário dos demais serviços, não toca o banco: monta a resposta a partir
do registro estático de providers (`app/providers/registry.py`), que é
justamente o que este endpoint existe para expor — a capacidade do backend,
não uma projeção de dados ingeridos.
"""

from __future__ import annotations

from app.models import DataContext
from app.models.context import DATA_CONTEXT_INFO
from app.providers.registry import providers_by_context
from app.schemas.context import ContextListResponse, ContextOut, ContextProviderOut


def list_contexts() -> ContextListResponse:
    contexts = []
    for context in DataContext:
        name, description = DATA_CONTEXT_INFO[context]
        providers = providers_by_context(context)
        contexts.append(
            ContextOut(
                key=context.value,
                name=name,
                description=description,
                providers=[
                    ContextProviderOut(
                        key=provider.key,
                        name=provider.name,
                        homepage=provider.homepage,
                        indicator_count=len(provider.provides),
                    )
                    for provider in providers
                ],
                indicator_count=sum(len(provider.provides) for provider in providers),
            )
        )
    return ContextListResponse(contexts=contexts)
