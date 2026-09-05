"""Subgrafo ReAct reutilizável, usado pelos nós especialistas via tools MCP."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.prebuilt import create_react_agent

from venus_sdk.mcp.tools import get_mcp_tools


def montar_agente_mcp(
    llm: BaseChatModel, *, prompt: str | None = None, tools: list[Any] | None = None
) -> Any:
    """Monta um agente ReAct reutilizável com as tools disponíveis.

    `tools`, se informado, é usado diretamente — é o caminho usado por
    produto/ingrediente hoje (`tools/produto.py`/`tools/ingrediente.py`,
    Postgres via asyncpg, montadas com o pool em
    `nodes/especialistas.py::montar_no_agente_produto`/
    `montar_no_agente_ingrediente`).

    Sem `tools`, cai no client MCP genérico (`get_mcp_tools()`) — ainda um
    stub (`NotImplementedError`), usado hoje por rotina e FAQ enquanto suas
    tools (Mongo/Qdrant) não são implementadas.
    """
    return create_react_agent(llm, tools=tools if tools is not None else get_mcp_tools(), prompt=prompt)
