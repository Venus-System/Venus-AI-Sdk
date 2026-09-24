"""SOMENTE-LEITURA: acha colunas cujo nome contém o texto dado (schema venus)."""
import asyncio, os, sys
import asyncpg
from dotenv import load_dotenv

async def main(txt):
    load_dotenv()
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    for r in await c.fetch("select table_name, column_name from information_schema.columns "
                           "where table_schema='venus' and column_name ilike $1 order by 1", f"%{txt}%"):
        print(dict(r))
    await c.close()
asyncio.run(main(sys.argv[1]))
