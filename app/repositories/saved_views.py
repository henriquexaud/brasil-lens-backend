"""Acesso a dados das visualizações salvas.

Único repositório de **escrita** do projeto. Usa o ORM (e não SQL textual como
os de leitura) porque aqui o volume é trivial e o que importa é o ciclo de vida
do objeto — as consultas de leitura usam SQL cru justamente porque lá o plano de
execução é o produto.

Como nos demais repositórios, nenhuma política mora aqui: quem decide o que é
404, 409 ou 400 é o serviço.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SavedView


async def list_views(session: AsyncSession, *, limit: int, offset: int) -> list[SavedView]:
    """Mais recentes primeiro — é a ordem útil para uma lista de marcadores."""
    stmt = (
        select(SavedView)
        .order_by(SavedView.created_at.desc(), SavedView.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars())


async def count_views(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(SavedView))).scalar_one()


async def get_by_public_id(session: AsyncSession, public_id: UUID) -> SavedView | None:
    stmt = select(SavedView).where(SavedView.public_id == public_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def name_is_taken(
    session: AsyncSession,
    name: str,
    *,
    exclude_id: int | None = None,
) -> bool:
    """Existe outra visualização com este nome, ignorando caixa?

    O UNIQUE do banco é sensível a caixa; esta checagem existe para o serviço
    poder devolver 409 com mensagem em vez de deixar o driver estourar um
    IntegrityError opaco — e para "Brasil" e "brasil" não coexistirem.
    `exclude_id` isenta a própria linha durante um PUT.
    """
    taken = select(SavedView.id).where(func.lower(SavedView.name) == name.strip().lower())
    if exclude_id is not None:
        taken = taken.where(SavedView.id != exclude_id)
    return bool((await session.execute(select(exists(taken)))).scalar())


async def persist(session: AsyncSession, view: SavedView) -> SavedView:
    """Grava a visualização e devolve o estado que o banco realmente tem.

    Serve tanto para uma instância nova quanto para uma já carregada e alterada:
    `session.add` é idempotente para objetos já na sessão. O `refresh` traz de
    volta o que só o banco sabe — `created_at` na criação, `updated_at` no
    `onupdate` — e é o que impede a resposta de devolver um timestamp inventado.
    O commit é da dependência `get_write_session`, não daqui.
    """
    session.add(view)
    await session.flush()
    await session.refresh(view)
    return view


async def delete_by_public_id(session: AsyncSession, public_id: UUID) -> int:
    """Devolve quantas linhas foram removidas (0 = não existia)."""
    result = await session.execute(delete(SavedView).where(SavedView.public_id == public_id))
    return result.rowcount or 0
