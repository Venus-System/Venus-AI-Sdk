"""Client MCP do Venus — carrega tools de servidores MCP (via
`langchain-mcp-adapters`) para os agentes.

Configuração (por ordem): argumento `servidores`, variável `MCP_SERVERS`
(JSON) ou arquivo `mcp_servers.json`. Formato de `MultiServerMCPClient`:

    {"venus": {"transport": "stdio", "command": "python",
               "args": ["-m", "venus_sdk.mcp.servidor"]},
     "externo": {"transport": "streamable_http", "url": "http://host:8765/mcp"}}
"""

from __future__ import annotations

import functools
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from venus_sdk.prompts.guardrail_rules import contem_tentativa_de_injecao

logger = logging.getLogger(__name__)

RESULTADO_DESCARTADO = json.dumps(
    {"erro": "a resposta da tool externa foi descartada por segurança"}, ensure_ascii=False
)


def config_padrao_venus() -> dict[str, Any]:
    """Servidor MCP do próprio Venus, em subprocesso stdio (herda o ambiente)."""
    return {"venus": {"transport": "stdio", "command": sys.executable,
                      "args": ["-m", "venus_sdk.mcp.servidor"], "env": dict(os.environ)}}


def carregar_config_mcp(servidores: dict[str, Any] | None = None) -> dict[str, Any]:
    """Configuração dos servidores MCP, na ordem de prioridade descrita no
    topo do módulo; sem nenhuma, usa o servidor do próprio Venus."""
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
    return [com_filtro_de_injecao(tool) for tool in tools if apenas is None or tool.name in apenas]


def _texto_do_conteudo(conteudo: Any) -> str:
    """Texto de um retorno de tool MCP: string, ou lista de strings/blocos `{"text": ...}`."""
    if isinstance(conteudo, str):
        return conteudo
    if isinstance(conteudo, list):
        return "\n".join(
            str(bloco.get("text") or "") if isinstance(bloco, dict) else str(bloco) for bloco in conteudo
        )
    return str(conteudo)


def com_filtro_de_injecao(tool: Any) -> Any:
    """Descarta o retorno da tool quando ele parece instrução ao sistema
    (injeção indireta), com o mesmo filtro da busca web e do A2A. O guardrail de
    entrada só vê a mensagem do usuário; o que um servidor MCP externo devolve
    chegava ao agente sem passar por nada."""
    original = getattr(tool, "coroutine", None)
    if original is None:
        return tool

    @functools.wraps(original)
    async def coroutine(*args: Any, **kwargs: Any) -> Any:
        resultado = await original(*args, **kwargs)
        # Tools do adapter usam `response_format="content_and_artifact"`.
        conteudo = resultado[0] if isinstance(resultado, tuple) else resultado
        if not contem_tentativa_de_injecao(_texto_do_conteudo(conteudo)):
            return resultado
        logger.warning("Resposta da tool MCP %s descartada: parece instrução ao sistema", tool.name)
        return (RESULTADO_DESCARTADO, None) if isinstance(resultado, tuple) else RESULTADO_DESCARTADO

    tool.coroutine = coroutine
    return tool
