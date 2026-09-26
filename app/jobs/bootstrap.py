"""Importa territórios e geometrias necessárias ao mapa climático."""

from __future__ import annotations

import argparse
import sys

from app.core.logging import get_logger
from app.jobs import import_geometries, import_territories
from app.jobs._runner import run_job

logger = get_logger(__name__)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Ingestão territorial do Brasil Lens.")
    parser.add_argument(
        "--skip-municipal-geometries",
        action="store_true",
        help="Pula a malha municipal (a etapa mais longa: ~60 MB em 27 requisições).",
    )
    parser.add_argument("--states", help="Limita a ingestão municipal às UFs informadas.")
    args = parser.parse_args()

    original_argv = sys.argv
    steps = [
        ("import_territories", import_territories, []),
        (
            "import_geometries",
            import_geometries,
            [
                *(["--skip-municipalities"] if args.skip_municipal_geometries else []),
                *(["--states", args.states] if args.states else []),
            ],
        ),
    ]
    for name, module, extra_args in steps:
        print(f"\n=== {name} {' '.join(extra_args)} ===")
        logger.info("bootstrap.step", extra={"step": name})
        sys.argv = [name, *extra_args]
        try:
            await module.main()
        finally:
            sys.argv = original_argv

    print("\nIngestão territorial completa.")
    return 0


if __name__ == "__main__":
    run_job(main)
