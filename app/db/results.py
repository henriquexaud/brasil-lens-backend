from __future__ import annotations

from typing import Any, cast

from sqlalchemy import CursorResult
from sqlalchemy.engine import Result


def affected_rows(result: Result[Any]) -> int:
    return cast("CursorResult[Any]", result).rowcount or 0
