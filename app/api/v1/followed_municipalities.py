"""Municípios seguidos pelo usuário (contexto Clima).

Separado das rotas de clima de propósito: carregar o tempo de um município e
saber se o usuário o acompanha são perguntas diferentes, com caches diferentes
— a primeira é projeção pública e cacheável, a segunda é pessoal e `no-store`.

    GET    /me/followed-municipalities          → lista (mais recentes primeiro)
    PUT    /me/followed-municipalities/{code}   → segue    201 (novo) / 200 (já seguia)
    DELETE /me/followed-municipalities/{code}   → deixa    204 (seguia ou não)

`/me` é o usuário da requisição (ver `get_current_user_id`). PUT e DELETE são
idempotentes: o recurso é o próprio município, então repetir o pedido depois
de uma atualização otimista no cliente nunca vira erro.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id, get_session, get_write_session
from app.core.config import settings
from app.schemas.followed_municipality import (
    FollowedMunicipalityListResponse,
    FollowedMunicipalityOut,
)
from app.services import followed_municipalities as followed_service

router = APIRouter(prefix="/me/followed-municipalities", tags=["followed-municipalities"])

UserId = Annotated[str, Depends(get_current_user_id)]
MunicipalityCode = Annotated[
    str,
    Path(pattern=r"^\d{7}$", description="Código IBGE do município (7 dígitos)."),
]


@router.get(
    "",
    response_model=FollowedMunicipalityListResponse,
    summary="Lista os municípios seguidos",
)
async def list_followed(
    response: Response,
    user_id: UserId,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FollowedMunicipalityListResponse:
    # Muda a cada seguir/deixar do próprio usuário: cache mentiria logo depois.
    response.headers["Cache-Control"] = "no-store"
    return await followed_service.list_followed(session, user_id)


@router.put(
    "/{municipality_code}",
    response_model=FollowedMunicipalityOut,
    responses={201: {"description": "Passou a seguir"}, 200: {"description": "Já seguia"}},
    summary="Segue um município",
)
async def follow(
    municipality_code: MunicipalityCode,
    response: Response,
    user_id: UserId,
    session: Annotated[AsyncSession, Depends(get_write_session)],
) -> FollowedMunicipalityOut:
    followed, created = await followed_service.follow(session, user_id, municipality_code)
    if created:
        response.status_code = status.HTTP_201_CREATED
        response.headers["Location"] = (
            f"{settings.api_v1_prefix}/me/followed-municipalities/{municipality_code}"
        )
    return followed


@router.delete(
    "/{municipality_code}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deixa de seguir um município",
)
async def unfollow(
    municipality_code: MunicipalityCode,
    user_id: UserId,
    session: Annotated[AsyncSession, Depends(get_write_session)],
) -> None:
    await followed_service.unfollow(session, user_id, municipality_code)
