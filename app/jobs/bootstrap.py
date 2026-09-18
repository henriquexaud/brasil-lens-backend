"""Executa a ingestão completa na ordem correta.

`python -m app.jobs.bootstrap [--skip-municipal-geometries] [--periods ...]`

A ordem não é arbitrária:

1. `seed_indicators` — o catálogo precisa existir antes dos valores (FK).
2. `import_territories` — territórios antes de geometrias e valores (FK).
3. `import_geometries` — geometria depende do território.
4. `import_indicators` — valores dependem de território e indicador, e os
   derivados são recalculados no final deste passo.

Cada etapa é um job independente e idempotente: se uma falhar, dá para
reexecutar só ela.
"""

from __future__ import annotations

import argparse
import sys

from app.core.logging import get_logger
from app.jobs import import_geometries, import_indicators, import_territories, seed_indicators
from app.jobs._runner import run_job

logger = get_logger(__name__)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Ingestão completa do Brasil Lens.")
    parser.add_argument(
        "--skip-municipal-geometries",
        action="store_true",
        help="Pula a malha municipal (a etapa mais longa: ~60 MB em 27 requisições).",
    )
    parser.add_argument(
        "--skip-municipal-indicators",
        action="store_true",
        help="Pula os valores municipais.",
    )
    parser.add_argument("--periods", help="Restringe os períodos dos indicadores (ex.: '2022').")
    parser.add_argument("--states", help="Limita as etapas municipais às UFs informadas.")
    args = parser.parse_args()

    steps: list[tuple[str, list[str]]] = [
        ("seed_indicators", []),
        ("import_territories", []),
        (
            "import_geometries",
            [
                *(["--skip-municipalities"] if args.skip_municipal_geometries else []),
                *(["--states", args.states] if args.states else []),
            ],
        ),
        (
            "import_indicators",
            [
                *(["--skip-municipalities"] if args.skip_municipal_indicators else []),
                *(["--periods", args.periods] if args.periods else []),
                *(["--states", args.states] if args.states else []),
            ],
        ),
    ]

    modules = {
        "seed_indicators": seed_indicators,
        "import_territories": import_territories,
        "import_geometries": import_geometries,
        "import_indicators": import_indicators,
    }

    original_argv = sys.argv
    for name, extra_args in steps:
        print(f"\n=== {name} {' '.join(extra_args)} ===")
        logger.info("bootstrap.step", extra={"step": name})
        # Os jobs leem os próprios argumentos via argparse; reescrevemos argv
        # para reutilizá-los sem duplicar a definição das opções.
        sys.argv = [name, *extra_args]
        try:
            await modules[name].main()
        finally:
            sys.argv = original_argv

    print("\nIngestão completa.")
    return 0


if __name__ == "__main__":
    run_job(main)
