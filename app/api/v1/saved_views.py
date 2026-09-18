"""Visualizações salvas: o CRUD do usuário sobre recortes do mapa.

É a única família de rotas que escreve no banco — todo o resto da API é
projeção de leitura sobre o que a ingestão trouxe do IBGE. São quatro
operações e quatro métodos HTTP, cada um com a semântica padrão:

    GET    /views          → lista (mais recentes primeiro)
    POST   /views          → cria            201 + Location
    GET    /views/{id}     → detalha
    PUT    /views/{id}     → substitui       200
    DELETE /views/{id}     → remove          204

`{id}` é o UUID público da visualização; o id serial do banco não aparece no
contrato, como no resto da API.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session, get_write_session
from app.core.config import settings
from app.schemas.saved_view import (
    SavedViewCreate,
    SavedViewListResponse,
    SavedViewOut,
    SavedViewUpdate,
)
from app.services import saved_views as saved_views_service

router = APIRouter(prefix="/views", tags=["saved-views"])

ViewId = Annotated[UUID, Path(description="Identificador público (UUID) da visualização.")]


@router.get("", response_model=SavedViewListResponse, summary="Lista visualizações salvas")
async def list_views(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SavedViewListResponse:
    # Ao contrário das rotas de mapa e catálogo, esta muda a qualquer POST/PUT
    # do próprio usuário: cachear no cliente faria a lista mentir logo após
    # uma edição.
    response.headers["Cache-Control"] = "no-store"
    return await saved_views_service.list_views(session, limit=limit, offset=offset)


@router.post(
    "",
    response_model=SavedViewOut,
    status_code=status.HTTP_201_CREATED,
    summary="Cria uma visualização",
)
async def create_view(
    payload: SavedViewCreate,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_write_session)],
) -> SavedViewOut:
    view = await saved_views_service.create_view(session, payload)
    response.headers["Location"] = f"{settings.api_v1_prefix}/views/{view.id}"
    return view


@router.get("/{view_id}", response_model=SavedViewOut, summary="Detalha uma visualização")
async def get_view(
    view_id: ViewId,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SavedViewOut:
    response.headers["Cache-Control"] = "no-store"
    return await saved_views_service.get_view(session, view_id)


@router.put("/{view_id}", response_model=SavedViewOut, summary="Substitui uma visualização")
async def update_view(
    view_id: ViewId,
    payload: SavedViewUpdate,
    session: Annotated[AsyncSession, Depends(get_write_session)],
) -> SavedViewOut:
    return await saved_views_service.update_view(session, view_id, payload)


@router.delete(
    "/{view_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove uma visualização",
)
async def delete_view(
    view_id: ViewId,
    session: Annotated[AsyncSession, Depends(get_write_session)],
) -> None:
    await saved_views_service.delete_view(session, view_id)
