"""Lista colunas (SOMENTE-LEITURA) de tabelas do schema venus + 2 linhas de amostra."""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv


async def main(tabelas: list[str]) -> None:
    load_dotenv()
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], timeout=20)
    try:
        for t in tabelas:
            cols = await conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema='venus' AND table_name=$1 ORDER BY ordinal_position", t)
            print(f"\n## {t}: " + ", ".join(f"{c['column_name']}:{c['data_type']}" for c in cols))
            for r in await conn.fetch(f'SELECT * FROM venus."{t}" LIMIT 2'):
                print("   ", {k: (str(v)[:40] if v is not None else None) for k, v in dict(r).items()})
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
