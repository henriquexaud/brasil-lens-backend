from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session, get_write_session, require_trusted_write
from app.core.auth import SESSION_COOKIE, SESSION_SECONDS
from app.core.config import settings
from app.models import User
from app.schemas.auth import (
    AccountDeletion,
    AccountExport,
    LoginRequest,
    RegisterRequest,
    ThemeUpdate,
    UserOut,
)
from app.services import auth

router = APIRouter(tags=["auth"], dependencies=[Depends(require_trusted_write)])
WriteSession = Annotated[AsyncSession, Depends(get_write_session)]
ReadSession = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _set_session(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


@router.post(
    "/auth/register", response_model=UserOut, status_code=201, summary="Cria uma conta e entra"
)
async def register(
    body: RegisterRequest, request: Request, response: Response, session: WriteSession
) -> UserOut:
    user, token = await auth.register(
        session, body, request.cookies.get(SESSION_COOKIE), _client_ip(request)
    )
    _set_session(response, token)
    return user


@router.post("/auth/login", response_model=UserOut, summary="Entra na conta")
async def login(
    body: LoginRequest, request: Request, response: Response, session: WriteSession
) -> UserOut:
    user, token = await auth.login(
        session, body, request.cookies.get(SESSION_COOKIE), _client_ip(request)
    )
    _set_session(response, token)
    return user


def _clear_session(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"


@router.post("/auth/logout", status_code=204, summary="Encerra a sessão")
async def logout(request: Request, response: Response, session: WriteSession) -> None:
    await auth.logout(session, request.cookies.get(SESSION_COOKIE))
    _clear_session(response)


@router.get("/auth/me", response_model=UserOut, summary="Recupera a conta e o tema")
async def me(response: Response, user: CurrentUser) -> UserOut:
    response.headers["Cache-Control"] = "no-store"
    return auth.to_schema(user)


@router.put("/me/preferences", response_model=UserOut, summary="Salva o tema da conta")
async def preferences(
    body: ThemeUpdate, response: Response, user: CurrentUser, session: WriteSession
) -> UserOut:
    response.headers["Cache-Control"] = "no-store"
    return await auth.update_theme(session, user.id, body.theme)


@router.post(
    "/me/terms", response_model=UserOut, summary="Aceita a versão vigente da Política e dos Termos"
)
async def accept_terms(response: Response, user: CurrentUser, session: WriteSession) -> UserOut:
    response.headers["Cache-Control"] = "no-store"
    return await auth.accept_terms(session, user.id)


@router.get("/me/data", response_model=AccountExport, summary="Exporta os dados pessoais da conta")
async def export_data(response: Response, user: CurrentUser, session: ReadSession) -> AccountExport:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Disposition"] = 'attachment; filename="brasil-lens-meus-dados.json"'
    return await auth.export_account(session, user)


@router.post("/me/delete", status_code=204, summary="Exclui a conta e todos os dados dela")
async def delete_account(
    body: AccountDeletion,
    request: Request,
    response: Response,
    user: CurrentUser,
    session: WriteSession,
) -> None:
    await auth.delete_account(session, user, body.password, _client_ip(request))
    _clear_session(response)
