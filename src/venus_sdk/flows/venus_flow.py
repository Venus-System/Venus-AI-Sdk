"""Monta o StateGraph principal do Venus.

Liga guardrail de entrada -> roteador -> especialista -> agente juiz ->
orquestrador -> guardrail de saída. O FAQ é a exceção: responde direto e vai
para o guardrail de saída sem passar pelo Agente Juiz (ver
`prompts/faq.py`).
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from venus_sdk.nodes.especialistas import (
    no_agente_faq,
    no_agente_ingrediente,
    no_agente_produto,
    no_agente_rotina,
)
from venus_sdk.nodes.guardrails import no_guardrail_entrada, no_guardrail_saida
from venus_sdk.nodes.juiz import decidir_pos_juiz, no_agente_juiz
from venus_sdk.nodes.orquestrador import no_orquestrador
from venus_sdk.nodes.roteador import decidir_especialista, no_roteador
from venus_sdk.state import EstadoVenus


def montar_grafo_venus() -> StateGraph:
    """Fábrica do grafo principal do Venus.

    A topologia abaixo já reflete a arquitetura descrita nos prompts; a
    lógica de cada nó (em `nodes/`) ainda é um esqueleto (`NotImplementedError`).

    TODO: revisar as condições de roteamento (`decidir_especialista`,
    `decidir_pos_juiz`) e adicionar checkpointer/compile() conforme a
    necessidade de persistência de memória entre execuções.
    """
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
    grafo.add_edge("guardrail_entrada", "roteador")

    grafo.add_conditional_edges(
        "roteador",
        decidir_especialista,
        {
            "produto": "agente_produto",
            "ingrediente": "agente_ingrediente",
            "rotina": "agente_rotina",
            "faq": "agente_faq",
        },
    )

    # FAQ responde direto ao usuário; os demais especialistas passam pelo juiz.
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
