"""Dependências das rotas."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionFactory


async def get_session() -> AsyncIterator[AsyncSession]:
    """Sessão somente-leitura, uma por requisição.

    Não há commit aqui de propósito: as rotas de leitura não escrevem, e um
    commit implícito esconderia uma escrita acidental em um caminho GET.
    """
    async with SessionFactory() as session:
        yield session


async def get_write_session() -> AsyncIterator[AsyncSession]:
    """Sessão transacional para as rotas que escrevem (POST/PUT/DELETE).

    Commit ao final do handler, rollback em qualquer exceção — inclusive nas de
    domínio, que viram resposta de erro. A transação cobre a requisição inteira,
    então uma validação que falha depois de um flush não deixa linha órfã.
    """
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Ainda não há autenticação: todo vínculo pessoal (hoje, só os municípios
# seguidos) é gravado sob este usuário local. É um valor, não uma ausência, para
# que a coluna `user_id` já nasça NOT NULL e a UNIQUE `(user_id, …)` já valha.
LOCAL_USER_ID = "local"


async def get_current_user_id() -> str:
    """Identificador do usuário da requisição.

    O único ponto que muda quando a autenticação chegar: passa a ler o token e
    devolver o `sub` do provedor (ou 401). Rotas e serviços já recebem o
    usuário por aqui e não precisam saber de onde ele veio.
    """
    return LOCAL_USER_ID
