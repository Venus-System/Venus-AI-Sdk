"""Subgrafo ReAct reutilizável, usado pelos nós especialistas."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, ToolCallLimitMiddleware, dynamic_prompt
from langchain_core.language_models import BaseChatModel

# Começo do texto da `ToolMessage` que o `ToolCallLimitMiddleware` devolve no
# lugar de uma chamada bloqueada — `nodes/especialistas.py::_extrair_evidencias_tools`
# a deixa de fora da evidência: não é dado de domínio, e o Juiz a lia como
# "a tool deu erro".
CHAMADA_RECUSADA = "Tool call limit exceeded"


def montar_agente_mcp(llm: BaseChatModel, *, prompt: str | Callable[[], str] | None = None,
                      tools: list[Any], max_chamadas_tools: int | None = None) -> Any:
    """Monta um agente ReAct com as `tools` informadas — tools Postgres
    (`tools/produto.py`...), RAG (`tools/faq.py`) e/ou tools MCP/A2A já
    carregadas (`mcp/tools.py::get_mcp_tools`, `a2a_client.py`). Tools MCP são
    assíncronas: o agente deve ser invocado via `ainvoke` (o grafo já é).

    `prompt` é o system prompt: um texto fixo ou uma função sem argumentos
    que devolve o texto, chamada a cada vez que o LLM roda (o agente fica em
    cache, e o prompt leva a data/hora do momento — ver
    `nodes/especialistas.py::_prompt_com_data_atual`).

    `max_chamadas_tools` limita as chamadas de tool por execução: o prompt de
    produto pedia no máximo 5 e o agente fazia 8 a 11 (conversa real de
    2026-10-20), estourando a cota de tokens/min do Groq. As excedentes voltam
    como erro para o LLM, que segue e responde com o que já tem. A contagem é
    por execução (estado do agente), não um contador compartilhado: o agente
    fica em cache e atende várias conversas ao mesmo tempo."""
    if not tools:
        raise ValueError("montar_agente_mcp requer ao menos uma tool.")
    middleware: list[Any] = []
    if max_chamadas_tools is not None:
        middleware.append(ToolCallLimitMiddleware(run_limit=max_chamadas_tools, exit_behavior="continue"))
    if callable(prompt):
        return create_agent(llm, tools=tools, middleware=[_prompt_dinamico(prompt), *middleware])
    return create_agent(llm, tools=tools, system_prompt=prompt, middleware=middleware)


def _prompt_dinamico(montar_prompt: Callable[[], str]) -> Any:
    """Middleware que gera o system prompt na hora de cada chamada do LLM."""

    @dynamic_prompt
    def prompt_do_momento(request: ModelRequest) -> str:
        return montar_prompt()

    return prompt_do_momento
