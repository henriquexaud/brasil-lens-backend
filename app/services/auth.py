import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core import auth as security
from app.core.errors import AuthenticationError, ConflictError
from app.models import User
from app.repositories import users
from app.schemas.auth import LoginRequest, RegisterRequest, Theme, UserOut

# O mesmo trabalho de scrypt para e-mail inexistente evita expor contas pelo tempo de resposta.
_DUMMY_HASH = "scrypt$" + "00" * 16 + "$" + "00" * 32


def to_schema(user: User) -> UserOut:
    assert user.email is not None
    return UserOut.model_validate(user, from_attributes=True)


async def current_user(session: AsyncSession, token: str | None) -> User:
    user = await users.by_token(session, security.hash_token(token)) if token else None
    if user is None:
        raise AuthenticationError("Entre na sua conta para continuar.")
    return user


async def _start_session(
    session: AsyncSession, user: User, old_token: str | None
) -> tuple[UserOut, str]:
    if old_token:
        await users.remove_session(session, security.hash_token(old_token))
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(seconds=security.SESSION_SECONDS)
    await users.add_session(session, user.id, security.hash_token(token), expires)
    return to_schema(user), token


async def register(
    session: AsyncSession, body: RegisterRequest, old_token: str | None
) -> tuple[UserOut, str]:
    security.check_auth_attempt(body.email)
    password_hash = await run_in_threadpool(security.hash_password, body.password)
    user = await users.create(
        session,
        User(
            id=uuid.uuid4().hex,
            name=body.name,
            email=body.email,
            password_hash=password_hash,
            theme=body.theme,
        ),
    )
    if user is None:
        raise ConflictError("Este e-mail já está cadastrado. Entre na sua conta.")
    security.clear_auth_attempts(body.email)
    return await _start_session(session, user, old_token)


async def login(
    session: AsyncSession, body: LoginRequest, old_token: str | None
) -> tuple[UserOut, str]:
    security.check_auth_attempt(body.email)
    user = await users.by_email(session, body.email)
    valid = await run_in_threadpool(
        security.verify_password,
        body.password,
        (user.password_hash if user else None) or _DUMMY_HASH,
    )
    if user is None or not valid:
        raise AuthenticationError("E-mail ou senha incorretos.")
    security.clear_auth_attempts(body.email)
    return await _start_session(session, user, old_token)


async def logout(session: AsyncSession, token: str | None) -> None:
    if token:
        await users.remove_session(session, security.hash_token(token))


async def update_theme(session: AsyncSession, user_id: str, theme: Theme) -> UserOut:
    return to_schema(await users.set_theme(session, user_id, theme))
