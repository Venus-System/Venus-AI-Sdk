"""Testes dos nós de guardrail de entrada/saída (nodes/guardrails.py)."""

from __future__ import annotations

from venus_sdk.guardrail_rules import MENSAGEM_SAIDA_BLOQUEADA
from venus_sdk.nodes.guardrails import no_guardrail_saida


def test_no_guardrail_saida_remove_emoji_sem_bloquear() -> None:
    resultado = no_guardrail_saida({"resposta_final": "Oi! 👋 Como posso ajudar?"})

    assert resultado["saida_bloqueada"] is False
    assert resultado["resposta_final"] == "Oi! Como posso ajudar?"


def test_no_guardrail_saida_bloqueia_resposta_vazia() -> None:
    resultado = no_guardrail_saida({"resposta_final": ""})

    assert resultado["saida_bloqueada"] is True
    assert resultado["resposta_final"] == MENSAGEM_SAIDA_BLOQUEADA
