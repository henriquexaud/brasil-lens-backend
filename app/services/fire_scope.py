"""Recorte territorial e janela temporal compartilhados nas consultas ao INPE."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidParameterError, TerritoryNotFoundError
from app.repositories import territories
from app.schemas.fire_hotspots import FireScope


def iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def scope_filter(session: AsyncSession, level: FireScope, parent: str | None) -> str:
    # O país é obrigatório: o serviço também publica detecções fora do Brasil.
    if level == "country":
        if parent is not None:
            raise InvalidParameterError("O recorte Brasil não recebe um território pai.", "parent")
        return "id_0=33"
    expected_length = 2 if level == "state" else 7
    if not parent or len(parent) != expected_length or not parent.isascii() or not parent.isdigit():
        raise InvalidParameterError("Informe o código IBGE correspondente ao recorte.", "parent")
    territory = await territories.get_by_code(session, parent)
    if territory is None:
        raise TerritoryNotFoundError(parent)
    if territory.level.value != level:
        raise InvalidParameterError("O código IBGE não corresponde ao recorte.", "parent")
    field = "id_1" if level == "state" else "id_2"
    return f"id_0=33 AND {field}={int(parent)}"


def time_filter(scope: str, end: datetime, hours: int) -> str:
    return (
        f"{scope} AND data_hora_gmt >= '{iso(end - timedelta(hours=hours))}'"
        f" AND data_hora_gmt <= '{iso(end)}'"
    )
