"""Testes do nó Roteador e da aresta condicional decidir_especialista."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from venus_sdk.nodes.roteador import decidir_especialista, no_roteador


def _resposta_llm(texto: str) -> SimpleNamespace:
    return SimpleNamespace(content=texto)


@pytest.mark.parametrize(
    "rota, esperado",
    [
        ("produto", "produto"),
        ("ingrediente", "ingrediente"),
        ("rotina", "rotina"),
        ("faq", "faq"),
        (None, "direto"),
        ("fora_escopo", "direto"),
    ],
)
def test_decidir_especialista(rota: str | None, esperado: str) -> None:
    assert decidir_especialista({"rota": rota}) == esperado  # type: ignore[arg-type]


def test_no_roteador_encaminha_para_especialista() -> None:
    texto_llm = "ROUTE=produto\nPERGUNTA_ORIGINAL=por que esse produto foi recomendado?"
    with patch("venus_sdk.nodes.roteador.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm(texto_llm)
        resultado = no_roteador({"mensagem_usuario": "por que esse produto foi recomendado?"})

    assert resultado["rota"] == "produto"
    assert resultado["pergunta_original"] == "por que esse produto foi recomendado?"
    get_llm_mock.return_value.invoke.assert_called_once()


def test_no_roteador_responde_direto_em_small_talk() -> None:
    texto_llm = "Olá! Posso te ajudar com produtos, ingredientes ou rotina; por onde quer começar?"
    with patch("venus_sdk.nodes.roteador.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm(texto_llm)
        resultado = no_roteador({"mensagem_usuario": "oi, tudo bem?"})

    assert resultado["rota"] is None
    assert resultado["resposta_final"] == texto_llm


def test_no_roteador_tenta_de_novo_quando_llm_devolve_vazio() -> None:
    """Falha pontual do LLM (conteúdo vazio) não deve virar a mensagem
    genérica de saída bloqueada para algo simples como uma saudação —
    o roteador tenta mais uma vez antes de cair no fallback fixo."""
    texto_llm = "Oi! Como posso ajudar hoje?"
    with patch("venus_sdk.nodes.roteador.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.side_effect = [
            _resposta_llm(""),
            _resposta_llm(texto_llm),
        ]
        resultado = no_roteador({"mensagem_usuario": "oi"})

    assert resultado["rota"] is None
    assert resultado["resposta_final"] == texto_llm
    assert get_llm_mock.return_value.invoke.call_count == 2


def test_no_roteador_usa_fallback_quando_llm_falha_duas_vezes() -> None:
    with patch("venus_sdk.nodes.roteador.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.side_effect = [
            _resposta_llm(""),
            _resposta_llm("   "),
        ]
        resultado = no_roteador({"mensagem_usuario": "oi"})

    assert resultado["rota"] is None
    assert resultado["resposta_final"]  # nunca vazio
    assert get_llm_mock.return_value.invoke.call_count == 2
