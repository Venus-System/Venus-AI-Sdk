"""Monta o StateGraph principal do Venus.

Liga guardrail de entrada -> roteador -> especialista -> agente juiz ->
orquestrador -> guardrail de saída, com dois desvios previstos pelos
próprios prompts dos agentes:

- Entrada bloqueada pelo guardrail: pula roteador/especialistas/juiz/
  orquestrador e vai direto para o guardrail de saída (`nodes/guardrails.py`).
- Roteador responde diretamente (small talk ou fora de escopo, ver
  `prompts/router.py`): também pula para o guardrail de saída.

O FAQ é a outra exceção: responde direto e vai para o guardrail de saída sem
passar pelo Agente Juiz (ver `prompts/faq.py`).
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from venus_sdk.nodes.especialistas import (
    no_agente_faq,
    no_agente_ingrediente,
    no_agente_produto,
    no_agente_rotina,
)
from venus_sdk.nodes.guardrails import (
    decidir_pos_guardrail_entrada,
    no_guardrail_entrada,
    no_guardrail_saida,
)
from venus_sdk.nodes.juiz import decidir_pos_juiz, no_agente_juiz
from venus_sdk.nodes.orquestrador import no_orquestrador
from venus_sdk.nodes.roteador import decidir_especialista, no_roteador
from venus_sdk.state import EstadoVenus


def montar_grafo_venus() -> StateGraph:
    """Fábrica do grafo principal do Venus (não compilado — use
    `compilar_grafo_venus()` para obter um grafo executável)."""
    grafo = StateGraph(EstadoVenus)

    grafo.add_node("guardrail_entrada", no_guardrail_entrada)
    grafo.add_node("roteador", no_roteador)
    grafo.add_node("agente_produto", no_agente_produto)
    grafo.add_node("agente_ingrediente", no_agente_ingrediente)
    grafo.add_node("agente_rotina", no_agente_rotina)
    grafo.add_node("agente_faq", no_agente_faq)
    grafo.add_node("agente_juiz", no_agente_juiz)
    grafo.add_node("orquestrador", no_orquestrador)
    grafo.add_node("guardrail_saida", no_guardrail_saida)

    grafo.set_entry_point("guardrail_entrada")

    grafo.add_conditional_edges(
        "guardrail_entrada",
        decidir_pos_guardrail_entrada,
        {
            "bloqueado": "guardrail_saida",
            "liberado": "roteador",
        },
    )

    grafo.add_conditional_edges(
        "roteador",
        decidir_especialista,
        {
            "produto": "agente_produto",
            "ingrediente": "agente_ingrediente",
            "rotina": "agente_rotina",
            "faq": "agente_faq",
            "direto": "guardrail_saida",
        },
    )

    # FAQ e small talk/fora de escopo ("direto") já respondem por conta
    # própria; produto/ingrediente/rotina sempre passam pelo Agente Juiz.
    grafo.add_edge("agente_produto", "agente_juiz")
    grafo.add_edge("agente_ingrediente", "agente_juiz")
    grafo.add_edge("agente_rotina", "agente_juiz")
    grafo.add_edge("agente_faq", "guardrail_saida")

    grafo.add_conditional_edges(
        "agente_juiz",
        decidir_pos_juiz,
        {
            "aprovado": "orquestrador",
            "reprovado": "roteador",
            "esgotado": "orquestrador",
        },
    )

    grafo.add_edge("orquestrador", "guardrail_saida")
    grafo.add_edge("guardrail_saida", END)

    return grafo


def compilar_grafo_venus(checkpointer: Any | None = None) -> Any:
    """Compila o grafo principal do Venus.

    `checkpointer` é opcional (ex.: `memory.criar_checkpointer_em_memoria()`
    ou um saver persistente) — passe um valor quando precisar manter memória
    entre execuções separadas do grafo para a mesma conversa (`thread_id`,
    passado em `config={"configurable": {"thread_id": ...}}` no `invoke`);
    sem ele, o grafo roda stateless, e o histórico deve ser passado via
    `EstadoVenus.historico` a cada chamada.
    """
    return montar_grafo_venus().compile(checkpointer=checkpointer)
