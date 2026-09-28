"""Sobe a API: aplica as migrations e serve no mesmo processo.

Com `alembic upgrade head && uvicorn`, a pilha (SQLAlchemy, modelos, Pydantic) era
importada duas vezes; a 0,1 de CPU do Render grátis, isso custava ~19 s a cada subida.
"""

import os

import uvicorn

from alembic import command
from alembic.config import Config


def main() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
