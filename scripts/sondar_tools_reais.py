"""Chama as tools de LEITURA contra o DATABASE_URL (sem escrever nada) e
imprime um resumo — para ver como cada tool se comporta com os dados reais."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import asyncpg
from dotenv import load_dotenv

from venus_sdk.tools.compartilhadas import montar_tools_compartilhadas
from venus_sdk.tools.ingrediente import montar_tools_ingrediente
from venus_sdk.tools.produto import montar_tools_produto
from venus_sdk.tools.rotina import montar_tools_rotina

CASOS = [
    ("search_product", {"termo": "hidratante"}),
    ("search_product", {"termo": "produto bom pra cabelo cacheado"}),
    ("search_product", {"termo": "shampoo"}),
    ("search_product", {"termo": "cerave retinol"}),
    ("search_product", {"termo": "protetor solar"}),
    ("search_product", {"termo": "skate"}),
    ("get_product", {"product_id": 6}),
    ("get_product_score", {"product_id": 6}),
    ("get_product_ingredients", {"product_id": 6}),
    ("get_product_ingredients", {"product_id": 1}),
    ("get_personalized_score", {"product_id": 6, "user_id": 1}),
    ("search_ingredient", {"termo": "niacinamida"}),
    ("search_ingredient", {"termo": "acido hialuronico"}),
    ("search_ingredient", {"termo": "retinol"}),
    ("search_ingredient", {"termo": "parfum"}),
    ("get_user_allergies", {"user_id": 2}),
    ("get_user_allergies", {"user_id": 1}),
    ("get_user_profile", {"user_id": 1}),
    ("get_user_favorites", {"user_id": 1}),
    ("get_user_lists", {"user_id": 1}),
    ("suggest_routine", {"user_id": 1, "horario": "manha"}),
    ("suggest_routine", {"user_id": 2, "horario": "noite"}),
]


async def main() -> None:
    load_dotenv()
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2, timeout=30)
    ts = {t.name: t for f in (montar_tools_produto, montar_tools_ingrediente, montar_tools_compartilhadas,
                              montar_tools_rotina) for t in f(pool)}
    try:
        for nome, args in CASOS:
            try:
                r = await asyncio.wait_for(ts[nome].ainvoke(args), 30)
            except Exception as exc:  # noqa: BLE001
                r = f"EXCEÇÃO {type(exc).__name__}: {exc}"
            txt = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False, default=str)
            print(f"\n### {nome}({args})\n{txt[:int(sys.argv[1]) if len(sys.argv) > 1 else 420]}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
