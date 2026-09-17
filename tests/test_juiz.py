"""Testes do nó Agente Juiz e da aresta condicional decidir_pos_juiz."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from venus_sdk.nodes.juiz import MAX_TENTATIVAS_JUIZ, decidir_pos_juiz, no_agente_juiz


def _resposta_llm(texto: str) -> SimpleNamespace:
    return SimpleNamespace(content=texto)


@pytest.mark.parametrize(
    "estado, esperado",
    [
        ({"aprovado_juiz": True, "tentativas_juiz": 1}, "aprovado"),
        ({"aprovado_juiz": False, "tentativas_juiz": 1}, "reprovado"),
        ({"aprovado_juiz": False, "tentativas_juiz": MAX_TENTATIVAS_JUIZ}, "esgotado"),
    ],
)
def test_decidir_pos_juiz(estado: dict, esperado: str) -> None:
    assert decidir_pos_juiz(estado) == esperado  # type: ignore[arg-type]


def test_no_agente_juiz_aprova() -> None:
    with patch("venus_sdk.nodes.juiz.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm("RESULTADO=aprovado")
        resultado = no_agente_juiz(
            {
                "pergunta_original": "pergunta",
                "resposta_especialista": {"dominio": "produto"},
                "tentativas_juiz": 0,
            }
        )

    assert resultado["aprovado_juiz"] is True
    assert resultado["feedback_juiz"] is None
    assert resultado["tentativas_juiz"] == 1


def test_no_agente_juiz_reprova_com_feedback() -> None:
    texto_llm = "RESULTADO=reprovado\nFEEDBACK=faltou fonte para a afirmação."
    with patch("venus_sdk.nodes.juiz.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm(texto_llm)
        resultado = no_agente_juiz(
            {
                "pergunta_original": "pergunta",
                "resposta_especialista": {"dominio": "produto"},
                "tentativas_juiz": 0,
            }
        )

    assert resultado["aprovado_juiz"] is False
    assert resultado["feedback_juiz"] == "faltou fonte para a afirmação."
    assert resultado["tentativas_juiz"] == 1


def test_no_agente_juiz_acumula_tentativas() -> None:
    with patch("venus_sdk.nodes.juiz.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm("RESULTADO=reprovado\nFEEDBACK=corrija x.")
        resultado = no_agente_juiz(
            {
                "pergunta_original": "pergunta",
                "resposta_especialista": {"dominio": "produto"},
                "tentativas_juiz": 1,
            }
        )

    assert resultado["tentativas_juiz"] == 2


def test_no_agente_juiz_inclui_evidencias_tools_na_entrada_do_llm() -> None:
    """Sem `RESULTADOS_TOOLS=` na entrada, o Juiz só via o JSON final do
    especialista e não tinha como cruzar contra o que a tool citada em
    `fontes_usadas` realmente devolveu (achado do teste de conversa real em
    2026-09-10 — ver `EstadoVenus.evidencias_tools`)."""
    evidencias = [{"tool": "get_product_ingredients", "resultado": "[]"}]
    with patch("venus_sdk.nodes.juiz.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm("RESULTADO=aprovado")
        no_agente_juiz(
            {
                "pergunta_original": "pergunta",
                "resposta_especialista": {"dominio": "produto"},
                "evidencias_tools": evidencias,
                "tentativas_juiz": 0,
            }
        )

    mensagens = get_llm_mock.return_value.invoke.call_args[0][0]
    entrada_human = mensagens[1][1]
    assert "RESULTADOS_TOOLS=" in entrada_human
    assert "get_product_ingredients" in entrada_human


def test_no_agente_juiz_sem_evidencias_nao_inclui_resultados_tools() -> None:
    with patch("venus_sdk.nodes.juiz.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm("RESULTADO=aprovado")
        no_agente_juiz(
            {
                "pergunta_original": "pergunta",
                "resposta_especialista": {"dominio": "produto"},
                "tentativas_juiz": 0,
            }
        )

    mensagens = get_llm_mock.return_value.invoke.call_args[0][0]
    entrada_human = mensagens[1][1]
    assert "RESULTADOS_TOOLS=" not in entrada_human


def test_no_agente_juiz_tolera_colchetes_no_resultado_aprovado() -> None:
    """O prompt mostra o protocolo como `RESULTADO=[aprovado|reprovado]`
    (ver `prompts/juiz.py`); se o LLM ecoar o colchete ao pé da letra
    (`RESULTADO=[aprovado]`), o parser não pode tratar isso como reprovado."""
    with patch("venus_sdk.nodes.juiz.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm("RESULTADO=[aprovado]")
        resultado = no_agente_juiz(
            {
                "pergunta_original": "pergunta",
                "resposta_especialista": {"dominio": "produto"},
                "tentativas_juiz": 0,
            }
        )

    assert resultado["aprovado_juiz"] is True


def test_no_agente_juiz_tolera_colchetes_no_feedback() -> None:
    texto_llm = "RESULTADO=[reprovado]\nFEEDBACK=[faltou fonte para a afirmação.]"
    with patch("venus_sdk.nodes.juiz.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm(texto_llm)
        resultado = no_agente_juiz(
            {
                "pergunta_original": "pergunta",
                "resposta_especialista": {"dominio": "produto"},
                "tentativas_juiz": 0,
            }
        )

    assert resultado["aprovado_juiz"] is False
    assert resultado["feedback_juiz"] == "faltou fonte para a afirmação."


def test_no_agente_juiz_trata_falha_do_llm_como_reprovado_sem_derrubar_o_grafo() -> None:
    """Se o LLM do Juiz falhar (provedor indisponível), o nó não deve deixar
    a exceção subir crua até o `.ainvoke()` do grafo principal — vira uma
    reprovação sem feedback específico, reaproveitando o fluxo normal de
    retry/`esgotado` (ver `decidir_pos_juiz`)."""
    with patch("venus_sdk.nodes.juiz.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.side_effect = RuntimeError("provedor indisponível")
        resultado = no_agente_juiz(
            {
                "pergunta_original": "pergunta",
                "resposta_especialista": {"dominio": "produto"},
                "tentativas_juiz": 0,
            }
        )

    assert resultado["aprovado_juiz"] is False
    assert resultado["feedback_juiz"] is None
    assert resultado["tentativas_juiz"] == 1
