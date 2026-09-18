"""Idempotência da ingestão, exercitando o SQL real de upsert.

O SQL testado aqui é o mesmo objeto usado pelo job (`UPSERT_INDICATOR_VALUES_SQL`)
— duplicá-lo no teste testaria uma cópia, não a ingestão.

Cada teste roda dentro de uma transação que sofre rollback ao final, então não
depende de nem altera os dados ingeridos.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.import_indicators import UPSERT_INDICATOR_VALUES_SQL

pytestmark = pytest.mark.db

_TEST_CODE = "TEST0001"


@pytest_asyncio.fixture
async def fixture_ids(session: AsyncSession) -> AsyncIterator[dict[str, int]]:
    """Cria território, indicador e dataset isolados; desfaz tudo no final.

    A sessão já vem com transação implícita aberta (o conftest faz um SELECT),
    então basta dar rollback no final — nada é gravado no banco de verdade.
    """
    try:
        territory_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO territories (level, ibge_code, name)
                    VALUES ('country', :code, 'Território de teste')
                    RETURNING id
                    """
                ),
                {"code": _TEST_CODE},
            )
        ).scalar_one()
        indicator_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO indicators (key, name, unit, origin,
                                            decimal_places, display_order)
                    VALUES ('test_indicator', 'Indicador de teste', 'unit',
                            'sourced', 0, 999)
                    RETURNING id
                    """
                )
            )
        ).scalar_one()
        dataset_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO datasets (source, code, name)
                    VALUES ('test', 'test/dataset', 'Dataset de teste')
                    RETURNING id
                    """
                )
            )
        ).scalar_one()
        yield {
            "territory_id": territory_id,
            "indicator_id": indicator_id,
            "dataset_id": dataset_id,
        }
    finally:
        await session.rollback()


async def _upsert(
    session: AsyncSession,
    ids: dict[str, int],
    observations: list[tuple[str, int, Decimal]],
) -> int:
    result = await session.execute(
        UPSERT_INDICATOR_VALUES_SQL,
        {
            "indicator_id": ids["indicator_id"],
            "dataset_id": ids["dataset_id"],
            "ingestion_run_id": None,
            "codes": [code for code, _, _ in observations],
            "years": [year for _, year, _ in observations],
            "values": [value for _, _, value in observations],
        },
    )
    return result.rowcount or 0


async def _state(session: AsyncSession, ids: dict[str, int]) -> list[tuple[int, Decimal, object]]:
    rows = await session.execute(
        text(
            """
            SELECT reference_year, value, updated_at
              FROM indicator_values
             WHERE territory_id = :territory_id AND indicator_id = :indicator_id
             ORDER BY reference_year
            """
        ),
        ids,
    )
    return [(row.reference_year, row.value, row.updated_at) for row in rows]


async def test_reexecucao_com_dados_iguais_nao_duplica_nem_reescreve(
    session: AsyncSession, fixture_ids: dict[str, int]
) -> None:
    observations = [
        (_TEST_CODE, 2020, Decimal("100")),
        (_TEST_CODE, 2021, Decimal("110")),
    ]

    first = await _upsert(session, fixture_ids, observations)
    state_after_first = await _state(session, fixture_ids)

    second = await _upsert(session, fixture_ids, observations)
    state_after_second = await _state(session, fixture_ids)

    assert first == 2
    # A segunda passada não escreve nada: nem linha nova, nem updated_at novo.
    assert second == 0
    assert state_after_first == state_after_second
    assert len(state_after_second) == 2


async def test_valor_alterado_na_fonte_e_atualizado_no_lugar(
    session: AsyncSession, fixture_ids: dict[str, int]
) -> None:
    await _upsert(session, fixture_ids, [(_TEST_CODE, 2020, Decimal("100"))])

    changed = await _upsert(session, fixture_ids, [(_TEST_CODE, 2020, Decimal("123.456"))])
    state = await _state(session, fixture_ids)

    assert changed == 1
    # Revisão da fonte atualiza a linha existente — não cria uma segunda.
    assert len(state) == 1
    assert state[0][1] == Decimal("123.456000")


async def test_territorio_desconhecido_nao_cria_territorio_fantasma(
    session: AsyncSession, fixture_ids: dict[str, int]
) -> None:
    """Um valor nunca pode inventar território: a FK é real e o JOIN filtra."""
    written = await _upsert(
        session,
        fixture_ids,
        [("CODIGO_INEXISTENTE", 2020, Decimal("1"))],
    )

    assert written == 0
    assert await _state(session, fixture_ids) == []

    orphans = (
        await session.execute(
            text("SELECT COUNT(*) FROM territories WHERE ibge_code = 'CODIGO_INEXISTENTE'")
        )
    ).scalar_one()
    assert orphans == 0


async def test_chave_composta_impede_duplicata_no_mesmo_lote(
    session: AsyncSession, fixture_ids: dict[str, int]
) -> None:
    """Duas observações do mesmo (território, indicador, ano) no mesmo lote.

    O PostgreSQL não permite `ON CONFLICT DO UPDATE` atingindo a mesma linha
    duas vezes na mesma instrução — é exatamente essa restrição que garante que
    a chave natural é de fato única.
    """
    with pytest.raises(Exception, match="cannot affect row a second time"):
        await _upsert(
            session,
            fixture_ids,
            [(_TEST_CODE, 2020, Decimal("1")), (_TEST_CODE, 2020, Decimal("2"))],
        )
