"""Regras puras de guardrail — sem dependência do grafo/estado.

Consumidas pelos nós em `nodes/guardrails.py`.
"""

from __future__ import annotations


def guardrail_entrada(mensagem: str) -> tuple[bool, str | None]:
    """Valida a mensagem do usuário antes de entrar no grafo.

    Retorna (bloqueado, motivo). `motivo` é None quando não bloqueado.

    TODO: implementar as regras (ex.: conteúdo impróprio, prompt injection).
    """
    raise NotImplementedError


def guardrail_saida(resposta: str) -> tuple[bool, str | None]:
    """Valida a resposta final antes de devolvê-la ao usuário.

    TODO: implementar as regras (ex.: vazamento de dado sensível, PII).
    """
    raise NotImplementedError


def anonimizar_entrada(mensagem: str) -> str:
    """Remove/mascara dados sensíveis da mensagem do usuário antes de logar.

    TODO: implementar a anonimização (ex.: CPF, e-mail, telefone).
    """
    raise NotImplementedError
