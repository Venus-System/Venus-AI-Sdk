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
