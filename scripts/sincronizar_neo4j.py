"""Copia o catálogo, os favoritos (Postgres) e as regras de `data/checkup/`
para o Neo4j do check-up da rotina.

    python scripts/sincronizar_neo4j.py              # usa data/checkup/
    python scripts/sincronizar_neo4j.py --pasta X    # outra pasta de regras

Precisa de DATABASE_URL e NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD no .env. Só lê
o Postgres; o Neo4j é refeito (ver `venus_sdk/checkup/sincronizar.py`)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import asyncpg

from venus_sdk.checkup.sincronizar import PASTA_REGRAS_PADRAO, sincronizar
from venus_sdk.config.settings import DATABASE_URL
from venus_sdk.integrations.grafo_neo4j import executor_neo4j, get_neo4j_driver, neo4j_configurado


async def main(pasta: Path) -> int:
    if not DATABASE_URL or not neo4j_configurado():
        print("Defina DATABASE_URL e NEO4J_URI (+ NEO4J_USER/NEO4J_PASSWORD) no .env.")
        return 1
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=1)
    driver = get_neo4j_driver()
    try:
        contagem = await sincronizar(pool, executor_neo4j(driver), pasta)
    finally:
        await pool.close()
        await driver.close()
    for nome, quantidade in contagem.items():
        print(f"{nome:>22}: {quantidade}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[neo4j] %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pasta", type=Path, default=PASTA_REGRAS_PADRAO)
    sys.exit(asyncio.run(main(parser.parse_args().pasta)))
