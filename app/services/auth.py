import secrets
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core import auth as security
from app.core.errors import AuthenticationError, ConflictError, InvalidParameterError
from app.core.legal import TERMS_VERSION
from app.models import User
from app.repositories import users
from app.schemas.auth import (
    AccountExport,
    AccountRecord,
    DeviceRecord,
    FollowedRecord,
    LoginRequest,
    NotificationRecord,
    RegisterRequest,
    Theme,
    UserOut,
)

# O mesmo trabalho de scrypt para e-mail inexistente evita expor contas pelo tempo de resposta.
_DUMMY_HASH = "scrypt$" + "00" * 16 + "$" + "00" * 32


def to_schema(user: User) -> UserOut:
    assert user.email is not None
    return UserOut(
        id=user.id,
        name=user.name,
        email=user.email,
        theme=user.theme,
        terms_accepted=user.terms_version == TERMS_VERSION,
    )


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
    session: AsyncSession, body: RegisterRequest, old_token: str | None, ip: str | None = None
) -> tuple[UserOut, str]:
    security.check_auth_attempt(body.email, ip)
    password_hash = await run_in_threadpool(security.hash_password, body.password)
    user = await users.create(
        session,
        User(
            id=uuid.uuid4().hex,
            name=body.name,
            email=body.email,
            password_hash=password_hash,
            theme=body.theme,
            terms_version=TERMS_VERSION,
            terms_accepted_at=datetime.now(UTC),
        ),
    )
    if user is None:
        raise ConflictError("Este e-mail já está cadastrado. Entre na sua conta.")
    security.clear_auth_attempts(body.email, ip)
    return await _start_session(session, user, old_token)


async def login(
    session: AsyncSession, body: LoginRequest, old_token: str | None, ip: str | None = None
) -> tuple[UserOut, str]:
    security.check_auth_attempt(body.email, ip)
    user = await users.by_email(session, body.email)
    valid = await run_in_threadpool(
        security.verify_password,
        body.password,
        (user.password_hash if user else None) or _DUMMY_HASH,
    )
    if user is None or not valid:
        security.record_auth_failure(body.email)
        raise AuthenticationError("E-mail ou senha incorretos.")
    security.clear_auth_attempts(body.email, ip)
    return await _start_session(session, user, old_token)


async def logout(session: AsyncSession, token: str | None) -> None:
    if token:
        await users.remove_session(session, security.hash_token(token))


async def update_theme(session: AsyncSession, user_id: str, theme: Theme) -> UserOut:
    return to_schema(await users.set_theme(session, user_id, theme))


async def accept_terms(session: AsyncSession, user_id: str) -> UserOut:
    return to_schema(await users.accept_terms(session, user_id, TERMS_VERSION))


async def delete_account(
    session: AsyncSession, user: User, password: str, ip: str | None = None
) -> None:
    # A senha confirma que é o titular, e não alguém com a sessão aberta num aparelho alheio.
    assert user.email is not None and user.password_hash is not None
    security.check_auth_attempt(user.email, ip)
    if not await run_in_threadpool(security.verify_password, password, user.password_hash):
        security.record_auth_failure(user.email)
        raise InvalidParameterError("Senha incorreta.", parameter="password")
    security.clear_auth_attempts(user.email, ip)
    await users.remove(session, user.id)


async def export_account(session: AsyncSession, user: User) -> AccountExport:
    assert user.email is not None
    return AccountExport(
        exported_at=datetime.now(UTC),
        account=AccountRecord(
            id=user.id,
            name=user.name,
            email=user.email,
            theme=user.theme,
            created_at=user.created_at,
            terms_version=user.terms_version,
            terms_accepted_at=user.terms_accepted_at,
        ),
        followed_municipalities=[
            FollowedRecord.model_validate(row._asdict())
            for row in await users.followed_records(session, user.id)
        ],
        devices=[
            DeviceRecord(
                push_service=urlsplit(row.endpoint).hostname or "", registered_at=row.created_at
            )
            for row in await users.device_records(session, user.id)
        ],
        notifications=[
            NotificationRecord.model_validate(row._asdict())
            for row in await users.notification_records(session, user.id)
        ],
        active_sessions=await users.active_sessions(session, user.id),
    )
