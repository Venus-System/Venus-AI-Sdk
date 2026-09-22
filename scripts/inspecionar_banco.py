"""Inspeção SOMENTE-LEITURA do Postgres (DATABASE_URL): tabelas do schema
`venus`, contagens e amostras — para saber com que dados os testes reais vão
rodar. Não escreve nada."""

from __future__ import annotations

import asyncio
import os

import asyncpg
from dotenv import load_dotenv


async def main() -> None:
    load_dotenv()
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], timeout=20)
    try:
        tabelas = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='venus' ORDER BY 1"
        )
        print("TABELAS:", [t["table_name"] for t in tabelas])
        for t in tabelas:
            n = await conn.fetchval(f'SELECT count(*) FROM venus."{t["table_name"]}"')
            print(f"  {t['table_name']}: {n}")
        print("\nPRODUTOS (10):")
        for r in await conn.fetch(
            "SELECT p.product_id, p.name, b.name AS marca, pc.name AS categoria "
            "FROM venus.products p JOIN venus.brands b ON b.brand_id=p.fk_brand_id "
            "JOIN venus.product_categories pc ON pc.product_category_id=p.fk_product_category_id "
            "ORDER BY p.product_id LIMIT 10"
        ):
            print("  ", dict(r))
        print("\nPRODUTOS COM INGREDIENTES / SCORE:")
        print("  com ingredientes:", await conn.fetchval(
            "SELECT count(DISTINCT pv.fk_product_id) FROM venus.product_ingredients pi "
            "JOIN venus.product_versions pv ON pv.product_version_id=pi.fk_product_version_id WHERE pv.is_current"))
        print("  com score:", await conn.fetchval(
            "SELECT count(DISTINCT pv.fk_product_id) FROM venus.product_scores ps "
            "JOIN venus.product_versions pv ON pv.product_version_id=ps.fk_product_version_id WHERE pv.is_current"))
        print("\nINGREDIENTES (10):")
        for r in await conn.fetch("SELECT ingredient_id, common_name, inci_name FROM venus.ingredients ORDER BY 1 LIMIT 10"):
            print("  ", dict(r))
        print("\nUSUARIOS (5):")
        for r in await conn.fetch("SELECT * FROM venus.users ORDER BY 1 LIMIT 5"):
            print("  ", {k: (str(v)[:30]) for k, v in dict(r).items()})
        print("\nALERGIAS por usuário:")
        for r in await conn.fetch("SELECT fk_user_id, count(*) n FROM venus.user_allergies GROUP BY 1 ORDER BY 1 LIMIT 10"):
            print("  ", dict(r))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
