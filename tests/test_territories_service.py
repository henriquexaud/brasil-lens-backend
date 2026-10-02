from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.errors import TerritoryNotFoundError
from app.models import TerritoryLevel
from app.repositories import territories as repo
from app.repositories.territories import TerritoryRow
from app.services import territories as service


def _row(**overrides: object) -> TerritoryRow:
    values: dict[str, object] = {
        "ibge_code": "3550308",
        "name": "São Paulo",
        "level": TerritoryLevel.MUNICIPALITY,
        "abbreviation": None,
        "parent_ibge_code": "35",
        "parent_name": "São Paulo",
        "parent_level": TerritoryLevel.STATE,
        "capital_ibge_code": None,
        "capital_name": None,
        "bbox": None,
    }
    return TerritoryRow(**{**values, **overrides})  # type: ignore[arg-type]


async def test_listing_under_an_unknown_parent_is_not_found_and_never_queries_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repo, "get_level_by_code", AsyncMock(return_value=None))
    listing = AsyncMock()
    monkeypatch.setattr(repo, "list_territories", listing)

    with pytest.raises(TerritoryNotFoundError) as caught:
        await service.list_territories(AsyncMock(), parent_code="99")

    assert caught.value.details == {"ibgeCode": "99"}
    listing.assert_not_awaited()


async def test_listing_reports_total_and_the_page_that_was_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repo, "get_level_by_code", AsyncMock(return_value=TerritoryLevel.STATE))
    monkeypatch.setattr(repo, "list_territories", AsyncMock(return_value=[_row()]))
    monkeypatch.setattr(repo, "count_territories", AsyncMock(return_value=645))

    result = await service.list_territories(
        AsyncMock(), level=TerritoryLevel.MUNICIPALITY, parent_code="35", limit=1, offset=10
    )

    assert (result.pagination.total, result.pagination.limit, result.pagination.offset) == (
        645,
        1,
        10,
    )
    [item] = result.territories
    assert item.ibge_code == "3550308"
    assert item.parent is not None
    assert (item.parent.ibge_code, item.parent.name, item.parent.level) == (
        "35",
        "São Paulo",
        TerritoryLevel.STATE,
    )


async def test_a_territory_without_parent_has_no_parent_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    country = _row(
        ibge_code="1",
        name="Brasil",
        level=TerritoryLevel.COUNTRY,
        parent_ibge_code=None,
        parent_name=None,
        parent_level=None,
    )
    monkeypatch.setattr(repo, "list_territories", AsyncMock(return_value=[country]))
    monkeypatch.setattr(repo, "count_territories", AsyncMock(return_value=1))

    result = await service.list_territories(AsyncMock())

    assert result.territories[0].parent is None


async def test_detail_of_an_unknown_code_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repo, "get_by_code", AsyncMock(return_value=None))

    with pytest.raises(TerritoryNotFoundError, match="0000000"):
        await service.get_detail(AsyncMock(), "0000000")


async def test_state_detail_exposes_capital_children_and_bbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _row(
        ibge_code="35",
        name="São Paulo",
        level=TerritoryLevel.STATE,
        abbreviation="SP",
        parent_ibge_code="3",
        parent_name="Sudeste",
        parent_level=TerritoryLevel.REGION,
        capital_ibge_code="3550308",
        capital_name="São Paulo",
        bbox=(-53.1, -25.3, -44.2, -19.8),
    )
    monkeypatch.setattr(repo, "get_by_code", AsyncMock(return_value=state))
    monkeypatch.setattr(
        repo, "children_summary", AsyncMock(return_value=(645, TerritoryLevel.MUNICIPALITY))
    )

    detail = await service.get_detail(AsyncMock(), "35")

    assert detail.abbreviation == "SP"
    assert detail.capital is not None
    assert (detail.capital.ibge_code, detail.capital.level) == (
        "3550308",
        TerritoryLevel.MUNICIPALITY,
    )
    assert (detail.children_count, detail.children_level) == (645, TerritoryLevel.MUNICIPALITY)
    assert detail.bbox == (-53.1, -25.3, -44.2, -19.8)


async def test_municipality_detail_has_no_capital_and_no_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repo, "get_by_code", AsyncMock(return_value=_row()))
    monkeypatch.setattr(repo, "children_summary", AsyncMock(return_value=(0, None)))

    detail = await service.get_detail(AsyncMock(), "3550308")

    assert detail.capital is None
    assert (detail.children_count, detail.children_level) == (0, None)
