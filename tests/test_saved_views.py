"""Visualizações salvas — o único caminho de escrita da API.

Dois grupos, pela mesma divisão do resto da suíte:

* **unitário** — a normalização de `year`, que é a única regra do contrato com
  chance real de errar em silêncio ("latest" ↔ NULL ↔ ano);
* **integração** (`db`) — o ciclo POST → GET → PUT → DELETE contra o banco real,
  porque é lá que moram o UNIQUE do nome, os CHECKs e o rollback da transação.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import (
    IndicatorNotFoundError,
    InvalidParameterError,
    SavedViewNameTakenError,
    SavedViewNotFoundError,
)
from app.models import TerritoryLevel
from app.schemas.saved_view import (
    SavedViewCreate,
    SavedViewUpdate,
    column_to_year,
    normalize_year,
    year_to_column,
)
from app.services import saved_views as service

# ---------------------------------------------------------------- unitário --


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "latest"),
        ("", "latest"),
        ("latest", "latest"),
        ("LATEST", "latest"),
        ("  2022  ", "2022"),
        (2022, "2022"),
    ],
)
def test_normalize_year_accepts_the_same_values_as_the_map_route(
    raw: object, expected: str
) -> None:
    assert normalize_year(raw) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize("raw", ["19", "20222", "abc", "1899", "2101"])
def test_normalize_year_rejects_what_the_map_could_not_open(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_year(raw)


@pytest.mark.parametrize("year", ["latest", "2022"])
def test_year_round_trips_through_the_column(year: str) -> None:
    """Ida e volta sem perda: é o par que faria a coluna e o contrato divergirem."""
    assert column_to_year(year_to_column(year)) == year


def test_latest_is_null_in_the_column() -> None:
    """O sentinela vira NULL — e não o texto "latest" nem um ano inventado."""
    assert year_to_column("latest") is None
    assert year_to_column("2022") == 2022


# ------------------------------------------------------------- integração --


def _payload(name: str, **overrides: object) -> SavedViewCreate:
    body: dict[str, object] = {
        "name": name,
        "level": TerritoryLevel.STATE,
        "indicatorKey": "population",
        "year": "latest",
        "classes": 5,
    }
    body.update(overrides)
    return SavedViewCreate.model_validate(body)


@pytest.mark.db
async def test_crud_round_trip(session) -> None:  # type: ignore[no-untyped-def]
    """POST → GET → PUT → DELETE, o ciclo que o frontend executa.

    A sessão do teste nunca é commitada (o fixture a descarta), então a linha
    não sobrevive ao teste.
    """
    name = f"teste-{uuid.uuid4().hex[:8]}"

    created = await service.create_view(session, _payload(name))
    assert created.year == "latest"
    assert created.level is TerritoryLevel.STATE

    fetched = await service.get_view(session, created.id)
    assert fetched.id == created.id
    assert fetched.name == name

    listed = await service.list_views(session, limit=10, offset=0)
    assert created.id in {view.id for view in listed.views}
    # A paginação segue o mesmo contrato das listagens de território.
    assert listed.pagination.total >= 1
    assert (listed.pagination.limit, listed.pagination.offset) == (10, 0)

    updated = await service.update_view(
        session,
        created.id,
        SavedViewUpdate.model_validate(
            {
                "name": f"{name}-editado",
                "level": TerritoryLevel.MUNICIPALITY,
                "parentCode": "35",
                "indicatorKey": "population",
                "year": "2022",
                "classes": 7,
            }
        ),
    )
    # PUT é substituição: todos os campos acompanham, não só o nome.
    assert (updated.name, updated.year, updated.classes) == (f"{name}-editado", "2022", 7)
    assert updated.parent_code == "35"
    assert updated.created_at == created.created_at

    await service.delete_view(session, created.id)
    with pytest.raises(SavedViewNotFoundError):
        await service.get_view(session, created.id)

    await session.rollback()


@pytest.mark.db
async def test_name_is_unique_ignoring_case(session) -> None:  # type: ignore[no-untyped-def]
    name = f"teste-{uuid.uuid4().hex[:8]}"
    await service.create_view(session, _payload(name))
    with pytest.raises(SavedViewNameTakenError):
        await service.create_view(session, _payload(name.upper()))
    await session.rollback()


@pytest.mark.db
async def test_refuses_a_view_that_could_not_be_opened(session) -> None:  # type: ignore[no-untyped-def]
    """As validações da rota /map valem na escrita.

    Salvar um recorte que só falharia ao ser aberto seria gravar um defeito.
    """
    with pytest.raises(IndicatorNotFoundError):
        await service.create_view(session, _payload("x", indicatorKey="nao_existe"))

    with pytest.raises(InvalidParameterError):
        await service.create_view(
            session, _payload("x", level=TerritoryLevel.MUNICIPALITY, parentCode=None)
        )

    # Região não é pai de município — o pai de um município é uma UF.
    with pytest.raises(InvalidParameterError):
        await service.create_view(
            session, _payload("x", level=TerritoryLevel.MUNICIPALITY, parentCode="3")
        )

    await session.rollback()


@pytest.mark.db
async def test_delete_of_a_missing_view_is_404_not_silence(session) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SavedViewNotFoundError):
        await service.delete_view(session, uuid.uuid4())
