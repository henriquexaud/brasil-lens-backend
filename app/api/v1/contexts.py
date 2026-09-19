"""Descoberta de contextos de dados e dos providers que os alimentam.

Pensado para o futuro seletor de contexto do frontend: uma chamada devolve os
contextos disponíveis, sem o cliente precisar conhecer a lista de antemão.
"""

from fastapi import APIRouter, Response

from app.core.config import settings
from app.schemas.context import ContextListResponse
from app.services import contexts as contexts_service

router = APIRouter(prefix="/contexts", tags=["contexts"])


@router.get("", response_model=ContextListResponse, summary="Lista contextos de dados")
async def list_contexts(response: Response) -> ContextListResponse:
    # Metadado estático do registro de providers: não muda entre ingestões,
    # só entre deploys. Cache de cliente é seguro com a mesma política das
    # demais rotas de leitura.
    response.headers["Cache-Control"] = f"public, max-age={settings.http_cache_max_age}"
    return contexts_service.list_contexts()
