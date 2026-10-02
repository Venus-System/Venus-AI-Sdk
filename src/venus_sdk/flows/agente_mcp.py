"""Subgrafo ReAct reutilizável, usado pelos nós especialistas."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, dynamic_prompt
from langchain_core.language_models import BaseChatModel


def montar_agente_mcp(llm: BaseChatModel, *, prompt: str | Callable[[], str] | None = None,
                      tools: list[Any]) -> Any:
    """Monta um agente ReAct com as `tools` informadas — tools Postgres
    (`tools/produto.py`...), RAG (`tools/faq.py`) e/ou tools MCP/A2A já
    carregadas (`mcp/tools.py::get_mcp_tools`, `a2a_client.py`). Tools MCP são
    assíncronas: o agente deve ser invocado via `ainvoke` (o grafo já é).

    `prompt` é o system prompt: um texto fixo ou uma função sem argumentos
    que devolve o texto, chamada a cada vez que o LLM roda (o agente fica em
    cache, e o prompt leva a data/hora do momento — ver
    `nodes/especialistas.py::_prompt_com_data_atual`)."""
    if not tools:
        raise ValueError("montar_agente_mcp requer ao menos uma tool.")
    if callable(prompt):
        return create_agent(llm, tools=tools, middleware=[_prompt_dinamico(prompt)])
    return create_agent(llm, tools=tools, system_prompt=prompt)


def _prompt_dinamico(montar_prompt: Callable[[], str]) -> Any:
    """Middleware que gera o system prompt na hora de cada chamada do LLM."""

    @dynamic_prompt
    def prompt_do_momento(request: ModelRequest) -> str:
        return montar_prompt()

    return prompt_do_momento
