"""Caso de uso das visualizações salvas.

Aqui moram as regras que fazem uma visualização ser *abrível* depois de salva:

* o indicador precisa existir no catálogo;
* `level=municipality` exige um pai, e o pai precisa existir e ser uma UF;
* nomes são únicos (ignorando caixa), para a lista não virar um borrão de
  "Sem título", "Sem título (2)"…

São as mesmas validações da rota `/map`, aplicadas **na escrita**: salvar um
recorte que só falharia ao ser aberto seria salvar um defeito. A forma da
hierarquia que as sustenta é a mesma dos dois lados — vem de
`models/territory.py`, não de uma cópia local.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    IndicatorNotFoundError,
    InvalidParameterError,
    SavedViewNameTakenError,
    SavedViewNotFoundError,
    TerritoryNotFoundError,
)
from app.models import EXPECTED_PARENT_LEVEL, REQUIRES_PARENT, SavedView, TerritoryLevel
from app.repositories import indicators as indicators_repo
from app.repositories import saved_views as saved_views_repo
from app.repositories import territories as territories_repo
from app.schemas.common import Pagination
from app.schemas.saved_view import (
    SavedViewBase,
    SavedViewCreate,
    SavedViewListResponse,
    SavedViewOut,
    SavedViewUpdate,
    column_to_year,
    year_to_column,
)


def _to_schema(view: SavedView) -> SavedViewOut:
    return SavedViewOut(
        id=view.public_id,
        name=view.name,
        description=view.description,
        level=view.level,
        parent_code=view.parent_code,
        indicator_key=view.indicator_key,
        year=column_to_year(view.reference_year),
        classes=view.classes,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def _apply(view: SavedView, payload: SavedViewBase) -> SavedView:
    """Copia o recorte do corpo para a entidade.

    Usado por POST e por PUT: como PUT é substituição completa, os dois escrevem
    exatamente o mesmo conjunto de campos, e ter um só lugar que os lista impede
    que um campo novo entre na criação e seja esquecido na edição.
    """
    view.name = payload.name
    view.description = payload.description
    view.level = payload.level
    view.parent_code = payload.parent_code
    view.indicator_key = payload.indicator_key
    view.reference_year = year_to_column(payload.year)
    view.classes = payload.classes
    return view


async def _validate(
    session: AsyncSession,
    payload: SavedViewBase,
    *,
    exclude_id: int | None = None,
) -> None:
    """Recusa tudo que não poderia ser aberto depois — e nomes repetidos.

    `exclude_id` isenta a própria linha na edição: renomear para o mesmo nome
    não é conflito.
    """
    level, parent_code = payload.level, payload.parent_code

    if not await indicators_repo.list_definitions(session, key=payload.indicator_key):
        raise IndicatorNotFoundError(payload.indicator_key)

    if level in REQUIRES_PARENT and not parent_code:
        raise InvalidParameterError(
            f"Uma visualização de nível '{level.value}' precisa de 'parentCode' "
            "(ex.: parentCode=35 para os municípios de São Paulo).",
            parameter="parentCode",
        )
    if level is TerritoryLevel.COUNTRY and parent_code:
        raise InvalidParameterError(
            "O nível 'country' é a raiz da hierarquia e não aceita 'parentCode'.",
            parameter="parentCode",
        )

    if parent_code:
        parent_level = await territories_repo.get_level_by_code(session, parent_code)
        if parent_level is None:
            raise TerritoryNotFoundError(parent_code)
        expected = EXPECTED_PARENT_LEVEL.get(level)
        if expected is not None and parent_level != expected:
            raise InvalidParameterError(
                f"Para 'level={level.value}', 'parentCode' deve ser um território de nível "
                f"'{expected.value}', mas '{parent_code}' é '{parent_level.value}'.",
                parameter="parentCode",
            )

    if await saved_views_repo.name_is_taken(session, payload.name, exclude_id=exclude_id):
        raise SavedViewNameTakenError(payload.name)


async def _get_or_404(session: AsyncSession, view_id: UUID) -> SavedView:
    view = await saved_views_repo.get_by_public_id(session, view_id)
    if view is None:
        raise SavedViewNotFoundError(str(view_id))
    return view


async def list_views(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
) -> SavedViewListResponse:
    rows = await saved_views_repo.list_views(session, limit=limit, offset=offset)
    total = await saved_views_repo.count_views(session)
    return SavedViewListResponse(
        views=[_to_schema(row) for row in rows],
        pagination=Pagination(total=total, limit=limit, offset=offset),
    )


async def get_view(session: AsyncSession, view_id: UUID) -> SavedViewOut:
    return _to_schema(await _get_or_404(session, view_id))


async def create_view(session: AsyncSession, payload: SavedViewCreate) -> SavedViewOut:
    await _validate(session, payload)
    view = await saved_views_repo.persist(session, _apply(SavedView(), payload))
    return _to_schema(view)


async def update_view(
    session: AsyncSession,
    view_id: UUID,
    payload: SavedViewUpdate,
) -> SavedViewOut:
    view = await _get_or_404(session, view_id)
    await _validate(session, payload, exclude_id=view.id)
    return _to_schema(await saved_views_repo.persist(session, _apply(view, payload)))


async def delete_view(session: AsyncSession, view_id: UUID) -> None:
    removed = await saved_views_repo.delete_by_public_id(session, view_id)
    if removed == 0:
        # 404 e não 204: apagar algo que nunca existiu é um erro do cliente,
        # e silenciar isso esconderia um id errado na interface.
        raise SavedViewNotFoundError(str(view_id))
