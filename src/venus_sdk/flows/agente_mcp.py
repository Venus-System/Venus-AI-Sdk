"""Subgrafo ReAct reutilizável, usado pelos nós especialistas."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.prebuilt import create_react_agent


def montar_agente_mcp(llm: BaseChatModel, *, prompt: str | None = None, tools: list[Any]) -> Any:
    """Monta um agente ReAct com as `tools` informadas — tools Postgres
    (`tools/produto.py`...), RAG (`tools/faq.py`) e/ou tools MCP/A2A já
    carregadas (`mcp/tools.py::get_mcp_tools`, `a2a_client.py`). Tools MCP são
    assíncronas: o agente deve ser invocado via `ainvoke` (o grafo já é)."""
    if not tools:
        raise ValueError("montar_agente_mcp requer ao menos uma tool.")
    return create_react_agent(llm, tools=tools, prompt=prompt)
