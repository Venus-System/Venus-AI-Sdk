"""Subgrafo ReAct reutilizável, usado pelos nós especialistas via tools MCP."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.prebuilt import create_react_agent

from venus_sdk.mcp.tools import get_mcp_tools


def montar_agente_mcp(llm: BaseChatModel, *, prompt: str | None = None) -> Any:
    """Monta um agente ReAct reutilizável com as tools MCP disponíveis.

    Usado pelos nós em `nodes/especialistas.py` para consultar as tools de
    produto/ingrediente/rotina/FAQ conforme o prompt de cada especialista
    exigir.

    TODO: definir se cada especialista monta sua própria instância
    (filtrando as tools por domínio) ou se um único agente MCP genérico é
    reutilizado entre eles.
    """
    tools = get_mcp_tools()
    return create_react_agent(llm, tools=tools, prompt=prompt)
