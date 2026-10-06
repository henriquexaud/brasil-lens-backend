from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id, get_write_session, require_trusted_write
from app.core.config import settings
from app.schemas.notification import PushConfig, PushEndpoint, PushRegistration
from app.services import notifications

router = APIRouter(
    prefix="/me/notifications",
    tags=["notifications"],
    dependencies=[Depends(require_trusted_write)],
)
UserId = Annotated[str, Depends(get_current_user_id)]
WriteSession = Annotated[AsyncSession, Depends(get_write_session)]


@router.get("/config", response_model=PushConfig, summary="Configuração pública de notificações")
async def config(response: Response, user_id: UserId) -> PushConfig:
    response.headers["Cache-Control"] = "no-store"
    return PushConfig(public_key=settings.vapid_public_key or None)


@router.post("/subscriptions", status_code=204, summary="Autoriza notificações neste dispositivo")
async def subscribe(body: PushRegistration, user_id: UserId, session: WriteSession) -> None:
    await notifications.register_device(session, user_id, body)


@router.post("/unsubscribe", status_code=204, summary="Desativa notificações neste dispositivo")
async def unsubscribe(body: PushEndpoint, user_id: UserId, session: WriteSession) -> None:
    await notifications.unregister_device(session, user_id, body.endpoint)
