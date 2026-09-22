"""Client MCP do Venus — carrega tools de servidores MCP (via
`langchain-mcp-adapters`) para os agentes.

Configuração (por ordem): argumento `servidores`, variável `MCP_SERVERS`
(JSON) ou arquivo `mcp_servers.json`. Formato de `MultiServerMCPClient`:

    {"venus": {"transport": "stdio", "command": "python",
               "args": ["-m", "venus_sdk.mcp.servidor"]},
     "externo": {"transport": "streamable_http", "url": "http://host:8765/mcp"}}
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def config_padrao_venus() -> dict[str, Any]:
    """Servidor MCP do próprio Venus, em subprocesso stdio (herda o ambiente)."""
    return {"venus": {"transport": "stdio", "command": sys.executable,
                      "args": ["-m", "venus_sdk.mcp.servidor"], "env": dict(os.environ)}}


def carregar_config_mcp(servidores: dict[str, Any] | None = None) -> dict[str, Any]:
    if servidores:
        return servidores
    bruto = os.getenv("MCP_SERVERS")
    if bruto:
        return json.loads(bruto)
    arquivo = Path("mcp_servers.json")
    if arquivo.is_file():
        return json.loads(arquivo.read_text(encoding="utf-8"))
    return config_padrao_venus()


def get_mcp_client(servidores: dict[str, Any] | None = None) -> Any:
    """Cria um `MultiServerMCPClient` para a configuração resolvida."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    return MultiServerMCPClient(carregar_config_mcp(servidores))


async def get_mcp_tools(servidores: dict[str, Any] | None = None, *,
                        apenas: set[str] | None = None) -> list[Any]:
    """Carrega (async) as tools expostas pelos servidores MCP como tools
    LangChain. `apenas` filtra por nome. Servidor indisponível não derruba o
    chamador: devolve o que conseguiu (ou `[]`) e loga o erro."""
    try:
        tools = await get_mcp_client(servidores).get_tools()
    except Exception:  # noqa: BLE001
        logger.exception("Não consegui carregar tools MCP")
        return []
    return [t for t in tools if apenas is None or t.name in apenas]
