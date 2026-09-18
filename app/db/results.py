"""Auxiliares tipados para resultados de comandos."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import CursorResult
from sqlalchemy.engine import Result


def affected_rows(result: Result[Any]) -> int:
    """Linhas efetivamente afetadas por um INSERT/UPDATE.

    `Session.execute` é declarado como `Result`, que não expõe `rowcount`; o
    objeto concreto de um DML é sempre um `CursorResult`. O cast fica isolado
    aqui para não espalhar `type: ignore` pelos jobs — e `rowcount` é o número
    que prova a idempotência da ingestão (segunda execução devolve 0).
    """
    return cast("CursorResult[Any]", result).rowcount or 0
