"""SOMENTE-LEITURA: mostra os favoritos de um usuário e se o produto existe."""
import asyncio, os, sys
import asyncpg
from dotenv import load_dotenv

async def main(uid, pid):
    load_dotenv()
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    print([dict(r) for r in await c.fetch("select * from venus.favorites where fk_user_id=$1 and fk_product_id=$2", uid, pid)])
    await c.close()
asyncio.run(main(int(sys.argv[1]), int(sys.argv[2])))
