"""Cria o schema `venus` e carrega o seed de teste no Postgres de `DATABASE_URL`.

    docker compose up -d
    python scripts/init_db.py            # schema + seed
    python scripts/init_db.py --sem-seed # só o schema
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

SQL = Path(__file__).parent / "sql"


async def main(seed: bool) -> int:
    url = os.getenv("DATABASE_URL")
    if not url:
        try:
            from venus_sdk.config.settings import DATABASE_URL as url  # lê o .env
        except Exception:  # noqa: BLE001
            url = None
    if not url:
        print("Defina DATABASE_URL (ex.: postgresql://venus:venus@localhost:5432/venus).")
        return 1
    conn = await asyncpg.connect(url)
    try:
        await conn.execute((SQL / "schema.sql").read_text(encoding="utf-8"))
        print("schema aplicado")
        if seed:
            await conn.execute((SQL / "seed.sql").read_text(encoding="utf-8"))
            n = await conn.fetchval("SELECT count(*) FROM venus.products")
            print(f"seed carregado ({n} produtos)")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sem-seed", action="store_true")
    sys.exit(asyncio.run(main(not ap.parse_args().sem_seed)))
