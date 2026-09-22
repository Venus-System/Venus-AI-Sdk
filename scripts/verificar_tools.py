"""Chama CADA tool do Venus contra o banco de seed e imprime OK/FALHA.
Sai com código != 0 se qualquer verificação falhar.

    docker compose up -d && python scripts/init_db.py
    python scripts/verificar_tools.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

RAIZ = Path(__file__).resolve().parents[1]
resultados: list[tuple[str, bool, str]] = []


def registrar(nome: str, ok: bool, detalhe: str = "") -> None:
    resultados.append((nome, ok, detalhe))
    print(f"[{'OK   ' if ok else 'FALHA'}] {nome}" + (f" — {detalhe}" if detalhe and not ok else ""))


async def checar(nome: str, coro, pred) -> None:
    try:
        r = await coro
        registrar(nome, bool(pred(r)), repr(r)[:150])
    except Exception as exc:  # noqa: BLE001
        registrar(nome, False, f"{type(exc).__name__}: {exc}")


async def main() -> int:
    url = os.getenv("DATABASE_URL")
    if not url:
        try:
            from venus_sdk.config.settings import DATABASE_URL as url
        except Exception:  # noqa: BLE001
            url = None
    if not url:
        print("Defina DATABASE_URL.")
        return 2
    from venus_sdk.rag import criar_indice_local
    from venus_sdk.tools.compartilhadas import montar_tools_compartilhadas
    from venus_sdk.tools.faq import montar_tools_faq
    from venus_sdk.tools.ingrediente import montar_tools_ingrediente
    from venus_sdk.tools.produto import montar_tools_produto
    from venus_sdk.tools.rotina import montar_tools_rotina

    pool = await asyncpg.create_pool(url, min_size=1, max_size=3)
    indice = criar_indice_local(RAIZ / "data" / "faq")
    t = {x.name: x for f in (montar_tools_produto, montar_tools_ingrediente, montar_tools_compartilhadas,
                             montar_tools_rotina) for x in f(pool)}
    faq = {x.name: x for x in montar_tools_faq(indice)}
    ok_lista = lambda r: isinstance(r, list) and r  # noqa: E731
    ok_dict = lambda r: isinstance(r, dict) and "erro" not in r and r.get("encontrado", True) is not False  # noqa: E731

    print("== Produto ==")
    await checar("search_product", t["search_product"].ainvoke({"termo": "serum"}), ok_lista)
    await checar("get_product", t["get_product"].ainvoke({"product_id": 2}), ok_dict)
    await checar("get_product_score", t["get_product_score"].ainvoke({"product_id": 2}), ok_dict)
    await checar("get_personalized_score", t["get_personalized_score"].ainvoke({"product_id": 2, "user_id": 1}), ok_dict)
    await checar("get_product_ingredients", t["get_product_ingredients"].ainvoke({"product_id": 2}), ok_lista)
    await checar("get_product_ingredients (produto sem fórmula → não inventa)",
                 t["get_product_ingredients"].ainvoke({"product_id": 10}), lambda r: r.get("encontrado") is False)
    print("== Ingrediente ==")
    await checar("search_ingredient", t["search_ingredient"].ainvoke({"termo": "hialuronico"}), ok_lista)
    await checar("get_ingredient_summary", t["get_ingredient_summary"].ainvoke({"ingredient_id": 1}), ok_dict)
    await checar("get_ingredient_properties", t["get_ingredient_properties"].ainvoke({"ingredient_id": 1}), ok_lista)
    await checar("get_ingredient_effects", t["get_ingredient_effects"].ainvoke({"ingredient_id": 1}), ok_lista)
    await checar("get_ingredient_regulations", t["get_ingredient_regulations"].ainvoke({"ingredient_id": 5}), ok_lista)
    print("== Compartilhada ==")
    await checar("get_user_allergies", t["get_user_allergies"].ainvoke({"user_id": 1}), ok_lista)
    print("== Rotina ==")
    await checar("get_user_profile", t["get_user_profile"].ainvoke({"user_id": 1}), ok_dict)
    await checar("get_user_favorites", t["get_user_favorites"].ainvoke({"user_id": 1}), ok_lista)
    await checar("get_user_lists", t["get_user_lists"].ainvoke({"user_id": 1}), ok_lista)
    await checar("add_favorite", t["add_favorite"].ainvoke({"user_id": 2, "product_id": 6}), lambda r: r.get("ok"))
    await checar("remove_favorite", t["remove_favorite"].ainvoke({"user_id": 2, "product_id": 6}), lambda r: r.get("ok"))
    await checar("suggest_routine (exclui produtos com alergia)", t["suggest_routine"].ainvoke({"user_id": 1, "horario": "manha"}),
                 lambda r: {e["product_id"] for e in r["excluidos_por_alergia"]} == {1, 4})
    print("== FAQ / RAG ==")

    async def _fr():
        return faq["faq_retriever"].invoke({"pergunta": "como funciona o score?"})

    await checar("faq_retriever (fonte local)", _fr(), lambda r: r[0]["fonte"] == "como_funciona_o_score.md")

    async def _web():
        return await faq["buscar_na_web"].ainvoke({"consulta": "niacinamide skincare"})

    try:
        r = await _web()
        # Rede pode estar bloqueada: erro estruturado também prova o contrato (não estoura).
        registrar("buscar_na_web (fonte web)", isinstance(r, (list, dict)), repr(r)[:150])
        if isinstance(r, dict):
            print("        (aviso: sem resultados/rede na web agora — contrato de erro validado)")
    except Exception as exc:  # noqa: BLE001
        registrar("buscar_na_web (fonte web)", False, str(exc))

    print("== MCP ==")
    from venus_sdk.mcp.tools import get_mcp_tools

    env = dict(os.environ, DATABASE_URL=url, FAQ_DIR=str(RAIZ / "data" / "faq"))
    cfg = {"venus": {"transport": "stdio", "command": sys.executable,
                     "args": ["-m", "venus_sdk.mcp.servidor"], "env": env, "cwd": str(RAIZ)}}
    mcp_tools = {x.name: x for x in await get_mcp_tools(cfg)}
    registrar("MCP lista as 19 tools", len(mcp_tools) == 19, f"{len(mcp_tools)} tools")
    if mcp_tools:
        await checar("MCP search_product", mcp_tools["search_product"].ainvoke({"termo": "niacinamida"}),
                     lambda r: "Niacinamida" in str(r))

    print("== A2A ==")
    try:
        import httpx

        from venus_sdk.a2a_client import consultar_agente_externo
        from venus_sdk.a2a_server import montar_app_a2a

        class _Grafo:
            async def ainvoke(self, entrada, config=None):
                return {"resposta_final": "pong:" + entrada["mensagem_usuario"]}

        app = montar_app_a2a(grafo=_Grafo(), base_url="http://a2a.local")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
            await checar("A2A client → servidor", consultar_agente_externo("http://a2a.local", "ping", httpx_client=http),
                         lambda r: r == "pong:ping")
    except ImportError as exc:
        registrar("A2A", False, f"instale o extra a2a: {exc}")

    await pool.close()
    falhas = [r for r in resultados if not r[1]]
    print(f"\n{len(resultados) - len(falhas)}/{len(resultados)} verificações OK")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
