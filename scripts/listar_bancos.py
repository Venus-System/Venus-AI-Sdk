"""SOMENTE-LEITURA: mostra o nome do banco do DATABASE_URL (sem senha/host) e lista os bancos existentes."""
import asyncio, os
from urllib.parse import urlparse, urlunparse
import asyncpg
from dotenv import load_dotenv

async def main():
    load_dotenv()
    u = urlparse(os.environ["DATABASE_URL"])
    print("banco no .env:", repr(u.path.lstrip("/")))
    for alvo in ("defaultdb", "postgres"):
        try:
            c = await asyncpg.connect(urlunparse(u._replace(path="/" + alvo)), timeout=20)
        except Exception as e:  # noqa: BLE001
            print(f"  conectar em {alvo}: {type(e).__name__}"); continue
        print("bancos:", [r["datname"] for r in await c.fetch("select datname from pg_database where not datistemplate")])
        nomes = [r["datname"] for r in await c.fetch("select datname from pg_database where not datistemplate")]
        await c.close()
        for nome in nomes:  # quantos produtos/usuários cada banco tem no schema venus (só leitura)
            try:
                k = await asyncpg.connect(urlunparse(u._replace(path="/" + nome)), timeout=20)
                r = await k.fetchrow("select (select count(*) from venus.products) p, (select count(*) from venus.users) u")
                print(f"  {nome}: produtos={r['p']} usuarios={r['u']}")
                await k.close()
            except Exception as e:  # noqa: BLE001
                print(f"  {nome}: sem schema venus ({type(e).__name__})")
        break
asyncio.run(main())
