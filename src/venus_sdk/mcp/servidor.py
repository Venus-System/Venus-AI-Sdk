"""Servidor MCP do Venus (FastMCP) — expõe as tools do SDK para qualquer
cliente MCP (o próprio Venus via `mcp/tools.py`, Claude Desktop, etc.).

    python -m venus_sdk.mcp.servidor                       # stdio
    python -m venus_sdk.mcp.servidor --transport http --porta 8765

Variáveis: `DATABASE_URL` (Postgres, opcional — sem ela só as tools de FAQ
sobem) e `FAQ_DIR` (padrão `data/faq`)."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from venus_sdk.tools.compartilhadas import montar_tools_compartilhadas
from venus_sdk.tools.faq import montar_tools_faq
from venus_sdk.tools.ingrediente import montar_tools_ingrediente
from venus_sdk.tools.produto import montar_tools_produto
from venus_sdk.tools.rotina import montar_tools_rotina

_FABRICAS_COM_POOL = (
    montar_tools_produto,
    montar_tools_ingrediente,
    montar_tools_compartilhadas,
    montar_tools_rotina,
)
_PASTA_FAQ_PADRAO = "data/faq"
_PORTA_HTTP_PADRAO = 8765


def criar_servidor_mcp(*, pool: Any | None = None, indice: Any | None = None,
                       nome: str = "venus") -> FastMCP:
    """Monta o servidor registrando cada tool LangChain como tool MCP.
    `pool`/`indice` são injetados (o SDK não cria conexão sozinho); o que
    for `None` simplesmente não é exposto."""
    servidor = FastMCP(nome)
    tools: list[Any] = []
    if pool is not None:
        for fabrica in _FABRICAS_COM_POOL:
            tools += fabrica(pool)
    if indice is not None:
        tools += montar_tools_faq(indice)

    for tool in tools:
        _registrar(servidor, tool)
    return servidor


def _registrar(servidor: FastMCP, tool: Any) -> None:
    """Registra uma tool LangChain como tool MCP, preservando nome, descrição
    e o schema dos argumentos (via `args_schema`)."""

    async def _chamar(**kwargs: Any) -> str:
        resultado = await tool.ainvoke(kwargs)
        return json.dumps(resultado, ensure_ascii=False, default=str)

    # FastMCP infere o schema a partir da assinatura — montamos uma equivalente.
    campos = tool.args_schema.model_fields if tool.args_schema is not None else {}
    _chamar.__signature__ = _assinatura(campos)  # type: ignore[attr-defined]
    _chamar.__annotations__ = {nome: info.annotation for nome, info in campos.items()} | {"return": str}
    servidor.add_tool(_chamar, name=tool.name, description=tool.description)


def _assinatura(campos: dict[str, Any]) -> inspect.Signature:
    """Assinatura só com argumentos nomeados, obrigatórios primeiro."""
    parametros = []
    for nome, info in campos.items():
        padrao = inspect.Parameter.empty if info.is_required() else info.default
        parametros.append(inspect.Parameter(nome, inspect.Parameter.KEYWORD_ONLY, default=padrao,
                                            annotation=info.annotation))
    parametros.sort(key=lambda parametro: parametro.default is not inspect.Parameter.empty)
    return inspect.Signature(parametros, return_annotation=str)


async def _montar_do_ambiente() -> tuple[FastMCP, Any]:
    from venus_sdk.rag import criar_indice_local

    pool = None
    url = os.getenv("DATABASE_URL")
    if url:
        import asyncpg

        pool = await asyncpg.create_pool(url)
    faq_dir = Path(os.getenv("FAQ_DIR", _PASTA_FAQ_PADRAO))
    indice = criar_indice_local(faq_dir) if faq_dir.is_dir() else None
    return criar_servidor_mcp(pool=pool, indice=indice), pool


def main() -> None:  # pragma: no cover — entrypoint
    parser = argparse.ArgumentParser(description="Servidor MCP do Venus")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--porta", type=int, default=_PORTA_HTTP_PADRAO)
    args = parser.parse_args()

    async def _rodar() -> None:
        servidor, pool = await _montar_do_ambiente()
        try:
            if args.transport == "stdio":
                await servidor.run_stdio_async()
            else:
                servidor.settings.port = args.porta
                await servidor.run_streamable_http_async()
        finally:
            if pool is not None:
                await pool.close()

    asyncio.run(_rodar())


if __name__ == "__main__":  # pragma: no cover
    main()
