"""Atalho para `python -m venus_sdk.checkup.sincronizar` (mantido para quem
já usava o script). Copia o catálogo, os favoritos (Postgres) e as regras do
check-up para o Neo4j.

    python scripts/sincronizar_neo4j.py              # regras empacotadas no SDK
    python scripts/sincronizar_neo4j.py --pasta X    # outra pasta de regras"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from venus_sdk.checkup.sincronizar import PASTA_REGRAS_PADRAO, main

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[neo4j] %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pasta", type=Path, default=PASTA_REGRAS_PADRAO)
    sys.exit(asyncio.run(main(parser.parse_args().pasta)))
