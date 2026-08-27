"""Nós de guardrail de entrada e saída do grafo principal."""

from __future__ import annotations

from venus_sdk.guardrail_rules import anonimizar_entrada, guardrail_entrada, guardrail_saida
from venus_sdk.state import EstadoVenus


def no_guardrail_entrada(estado: EstadoVenus) -> EstadoVenus:
    """Aplica o guardrail de entrada e anonimiza a mensagem antes de logar.

    TODO: chamar `guardrail_entrada`/`anonimizar_entrada` e atualizar o
    estado (`entrada_bloqueada`, `motivo_bloqueio`, `mensagem_anonimizada`).
    """
    raise NotImplementedError


def no_guardrail_saida(estado: EstadoVenus) -> EstadoVenus:
    """Aplica o guardrail de saída sobre `resposta_final` antes de responder.

    TODO: chamar `guardrail_saida` e atualizar `saida_bloqueada` no estado.
    """
    raise NotImplementedError
