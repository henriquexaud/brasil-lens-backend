from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import SESSION_COOKIE
from app.core.config import settings
from app.core.errors import ForbiddenError
from app.db.session import SessionFactory
from app.models import User
from app.services import auth


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


def require_trusted_write(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    if request.headers.get("x-brasil-lens-client") != "web" or (
        origin is not None and origin not in settings.cors_origins
    ):
        raise ForbiddenError("Origem da solicitação não autorizada.")


async def get_write_session(
    _: Annotated[None, Depends(require_trusted_write)],
) -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_current_user(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> User:
    return await auth.current_user(session, request.cookies.get(SESSION_COOKIE))


async def get_current_user_id(user: Annotated[User, Depends(get_current_user)]) -> str:
    return user.id
