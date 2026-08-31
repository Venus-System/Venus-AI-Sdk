"""Nós de guardrail de entrada e saída do grafo principal."""

from __future__ import annotations

import logging
from typing import Literal

from venus_sdk.guardrail_rules import (
    MENSAGEM_ENTRADA_BLOQUEADA,
    MENSAGEM_SAIDA_BLOQUEADA,
    anonimizar_entrada,
    guardrail_entrada,
    guardrail_saida,
)
from venus_sdk.state import EstadoVenus

logger = logging.getLogger(__name__)

DecisaoGuardrailEntrada = Literal["bloqueado", "liberado"]


def no_guardrail_entrada(estado: EstadoVenus) -> EstadoVenus:
    """Aplica o guardrail de entrada e anonimiza a mensagem antes de logar."""
    mensagem = estado.get("mensagem_usuario", "") or ""
    bloqueado, motivo = guardrail_entrada(mensagem)

    if bloqueado:
        logger.info("Entrada bloqueada: %s", motivo)

    atualizacao: EstadoVenus = {
        "entrada_bloqueada": bloqueado,
        "motivo_bloqueio": motivo,
        "mensagem_anonimizada": anonimizar_entrada(mensagem),
    }
    if bloqueado:
        atualizacao["resposta_final"] = MENSAGEM_ENTRADA_BLOQUEADA
    return atualizacao


def decidir_pos_guardrail_entrada(estado: EstadoVenus) -> DecisaoGuardrailEntrada:
    """Aresta condicional: entrada bloqueada pula direto para o guardrail de
    saída, sem passar pelo roteador/especialistas/juiz/orquestrador."""
    return "bloqueado" if estado.get("entrada_bloqueada") else "liberado"


def no_guardrail_saida(estado: EstadoVenus) -> EstadoVenus:
    """Aplica o guardrail de saída sobre `resposta_final` antes de responder."""
    resposta = estado.get("resposta_final") or ""
    bloqueado, motivo = guardrail_saida(resposta)

    if bloqueado:
        logger.warning("Saída bloqueada: %s", motivo)

    atualizacao: EstadoVenus = {"saida_bloqueada": bloqueado}
    if bloqueado:
        atualizacao["resposta_final"] = MENSAGEM_SAIDA_BLOQUEADA
    return atualizacao
