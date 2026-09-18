"""Teste de integração: reprovação do Agente Juiz precisa voltar DIRETO pro
nó do especialista que gerou a resposta, sem passar de novo pelo roteador
(ver `nodes/juiz.py::decidir_pos_juiz` e `flows/venus_flow.py`).

Roda o grafo compilado de verdade, com o agente ReAct de produto substituído
por um fake controlável (mesmo espírito de `tests/test_especialistas.py`) —
evita precisar de um Postgres real só pra validar a aresta condicional."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from venus_sdk.flows.venus_flow import compilar_grafo_venus
from venus_sdk.memory import criar_checkpointer_em_memoria


def _resposta_llm(texto: str) -> SimpleNamespace:
    return SimpleNamespace(content=texto)


class _AgenteProdutoFalso:
    """Substitui o agente ReAct de produto — devolve um JSON fixo, contável
    por número de chamadas, sem tocar em Postgres/LLM de verdade."""

    def __init__(self, respostas_json: list[str]) -> None:
        self._respostas = list(respostas_json)
        self.chamadas = 0

    async def ainvoke(self, _entrada: dict) -> dict:
        self.chamadas += 1
        texto = self._respostas[min(self.chamadas, len(self._respostas)) - 1]
        return {"messages": [SimpleNamespace(content=texto, name=None)]}


def test_reprovacao_do_juiz_volta_direto_pro_especialista_sem_re_rotear() -> None:
    resposta_json = (
        '{"dominio":"produto","intencao":"explicar_recomendacao","resposta":"x",'
        '"recomendacao":"","fontes_usadas":["get_product"]}'
    )
    agente_falso = _AgenteProdutoFalso([resposta_json, resposta_json])

    with (
        patch("venus_sdk.nodes.especialistas.montar_agente_mcp", return_value=agente_falso),
        # `_agente_produto()` chama `get_llm_especialista()` pra montar o
        # argumento ANTES do mock acima interceptar a chamada — sem isto,
        # o teste tenta construir um `ChatGoogleGenerativeAI` de verdade e
        # quebra com `ValidationError` (API key) em qualquer ambiente sem
        # GEMINI_API_KEY configurada (ex.: CI, ver `.github/workflows/ci.yaml`).
        patch("venus_sdk.nodes.especialistas.get_llm_especialista", return_value=None),
        patch("venus_sdk.nodes.roteador.get_llm_roteador") as roteador_mock,
        patch("venus_sdk.nodes.juiz.get_llm_rapido") as juiz_mock,
    ):
        roteador_mock.return_value.invoke.return_value = _resposta_llm(
            "ROUTE=produto\nPERGUNTA_ORIGINAL=por que esse produto foi recomendado?"
        )
        juiz_mock.return_value.invoke.side_effect = [
            _resposta_llm("RESULTADO=reprovado\nFEEDBACK=cite a fonte direito."),
            _resposta_llm("RESULTADO=aprovado"),
        ]

        # pool não-None só pra passar do guard de `montar_tools_produto`/
        # `montar_tools_compartilhadas` (ver `tools/produto.py`) — nunca
        # usado de fato, já que `montar_agente_mcp` está substituído acima.
        grafo = compilar_grafo_venus(checkpointer=criar_checkpointer_em_memoria(), pool=object())
        estado_final = asyncio.run(
            grafo.ainvoke(
                {"mensagem_usuario": "por que esse produto foi recomendado pra mim?"},
                config={"configurable": {"thread_id": "conversa-retry"}},
            )
        )

    # O especialista rodou 2x (reprovado -> retry -> aprovado)...
    assert agente_falso.chamadas == 2
    # ...mas o roteador rodou só 1x — o retry não volta a classificar de novo.
    assert roteador_mock.return_value.invoke.call_count == 1
    assert estado_final["aprovado_juiz"] is True
