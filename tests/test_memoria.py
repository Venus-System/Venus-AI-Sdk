"""Teste de integração: o checkpointer em memória (`venus_sdk.memory`)
mantém o histórico entre chamadas separadas de `invoke` para o mesmo
`thread_id`, e não vaza entre `thread_id`s diferentes.

Usa o caminho de small talk (guardrail_entrada -> roteador -> "direto" ->
guardrail_saida), o único que já roda ponta a ponta sem depender do client
MCP (ainda não implementado — ver `mcp/tools.py`)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from venus_sdk.flows.venus_flow import compilar_grafo_venus
from venus_sdk.memory import criar_checkpointer_em_memoria


def _resposta_llm(texto: str) -> SimpleNamespace:
    return SimpleNamespace(content=texto)


def test_historico_persiste_entre_invokes_do_mesmo_thread_id() -> None:
    grafo = compilar_grafo_venus(checkpointer=criar_checkpointer_em_memoria())
    config = {"configurable": {"thread_id": "conversa-1"}}

    with patch("venus_sdk.nodes.roteador.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm("Oi! Como posso ajudar?")
        grafo.invoke({"mensagem_usuario": "oi"}, config=config)

        get_llm_mock.return_value.invoke.return_value = _resposta_llm("Claro, me conta mais.")
        estado_2 = grafo.invoke({"mensagem_usuario": "tudo bem?"}, config=config)

    historico = estado_2["historico"]
    assert [type(m) for m in historico] == [HumanMessage, AIMessage, HumanMessage, AIMessage]
    assert historico[0].content == "oi"
    assert historico[1].content == "Oi! Como posso ajudar?"
    assert historico[2].content == "tudo bem?"
    assert historico[3].content == "Claro, me conta mais."


def test_historico_nao_vaza_entre_thread_ids_diferentes() -> None:
    grafo = compilar_grafo_venus(checkpointer=criar_checkpointer_em_memoria())

    with patch("venus_sdk.nodes.roteador.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm("Oi!")
        grafo.invoke({"mensagem_usuario": "oi"}, config={"configurable": {"thread_id": "a"}})

        get_llm_mock.return_value.invoke.return_value = _resposta_llm("Olá!")
        estado_b = grafo.invoke(
            {"mensagem_usuario": "oi de novo"}, config={"configurable": {"thread_id": "b"}}
        )

    assert len(estado_b["historico"]) == 2
