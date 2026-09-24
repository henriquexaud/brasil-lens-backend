"""O caso de uso de localização preserva a ausência e o detalhe territorial."""

from unittest.mock import AsyncMock

import pytest

from app.core.errors import NotFoundError
from app.services import territories


async def test_locate_outside_brazil_keeps_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(territories, "locate", AsyncMock(return_value=None))

    with pytest.raises(
        NotFoundError, match="Não encontramos um município brasileiro nessa localização."
    ):
        await territories.locate_territory(AsyncMock(), -34.0, -47.0)


async def test_locate_reuses_territory_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    detail = object()
    find = AsyncMock(return_value="3550308")
    get_detail = AsyncMock(return_value=detail)
    monkeypatch.setattr(territories, "locate", find)
    monkeypatch.setattr(territories, "get_detail", get_detail)

    assert await territories.locate_territory(session, -23.55, -46.63) is detail
    find.assert_awaited_once_with(session, -23.55, -46.63)
    get_detail.assert_awaited_once_with(session, "3550308")
