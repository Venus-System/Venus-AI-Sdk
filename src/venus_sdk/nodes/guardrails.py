"""Nós de guardrail de entrada e saída do grafo principal."""

from __future__ import annotations

import logging
import os
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage

from venus_sdk.guardrail_rules import (
    MENSAGEM_ENTRADA_BLOQUEADA,
    MENSAGEM_SAIDA_BLOQUEADA,
    anonimizar_entrada,
    guardrail_entrada,
    guardrail_saida,
    remover_emojis,
)
from venus_sdk.llm.models import extrair_texto_resposta, get_llm_rapido
from venus_sdk.prompts.guardrail import GUARDRAIL_LLM_PROMPT
from venus_sdk.state import EstadoVenus

logger = logging.getLogger(__name__)

DecisaoGuardrailEntrada = Literal["bloqueado", "liberado"]


def no_guardrail_entrada(estado: EstadoVenus) -> EstadoVenus:
    """Aplica o guardrail de entrada, anonimiza a mensagem e a grava no
    histórico. Também zera o estado do Juiz: este nó roda uma vez por turno,
    e sem isso `tentativas_juiz` vinha do turno anterior pelo checkpointer."""
    mensagem = estado.get("mensagem_usuario", "") or ""
    bloqueado, motivo = guardrail_entrada(mensagem)
    if not bloqueado and _classificador_llm_ligado() and _classificador_llm_ve_injecao(mensagem):
        bloqueado, motivo = True, "tentativa de manipulação do sistema (classificador LLM)"
    mensagem_anonimizada = anonimizar_entrada(mensagem)

    atualizacao: EstadoVenus = {
        "entrada_bloqueada": bloqueado,
        "motivo_bloqueio": motivo,
        "mensagem_anonimizada": mensagem_anonimizada,
        "historico": [HumanMessage(content=mensagem_anonimizada)],
        "tentativas_juiz": 0,
        "aprovado_juiz": None,
        "feedback_juiz": None,
    }
    if bloqueado:
        logger.info("Entrada bloqueada: %s", motivo)
        atualizacao["resposta_final"] = MENSAGEM_ENTRADA_BLOQUEADA
        # O "sim" de um agendamento só vale na mensagem logo seguinte à proposta.
        atualizacao["agendamento_pendente"] = None
    return atualizacao


def _classificador_llm_ligado() -> bool:
    """Ligado por padrão; `VENUS_GUARDRAIL_LLM=0` desliga. Custa uma chamada
    de LLM rápido por mensagem que a regex não bloqueou."""
    return os.getenv("VENUS_GUARDRAIL_LLM", "1").strip() != "0"


def _classificador_llm_ve_injecao(mensagem: str) -> bool:
    """Segunda camada: um LLM barato responde SEGURO/INJECAO para o que a
    regex deixou passar. Se o LLM falhar, a mensagem passa (fail-open) — o
    classificador nunca derruba a conversa; os prompts dos agentes continuam
    recusando manipulação. A mensagem vai delimitada, como dado a classificar."""
    try:
        resposta = get_llm_rapido().invoke(
            [("system", GUARDRAIL_LLM_PROMPT), ("human", f"<mensagem>\n{mensagem}\n</mensagem>")]
        )
    except Exception:
        logger.warning("Classificador LLM do guardrail indisponível; mensagem liberada", exc_info=True)
        return False
    veredito = extrair_texto_resposta(resposta).strip().upper()
    return "INJECAO" in veredito or "INJEÇÃO" in veredito


def decidir_pos_guardrail_entrada(estado: EstadoVenus) -> DecisaoGuardrailEntrada:
    """Aresta condicional: entrada bloqueada pula direto para o guardrail de
    saída, sem passar pelo roteador/especialistas/juiz/orquestrador."""
    return "bloqueado" if estado.get("entrada_bloqueada") else "liberado"


def no_guardrail_saida(estado: EstadoVenus) -> EstadoVenus:
    """Remove emoji (a persona proíbe; o LLM nem sempre obedece), aplica o
    guardrail de saída e grava a resposta final no histórico."""
    resposta = remover_emojis(estado.get("resposta_final") or "")
    bloqueado, motivo = guardrail_saida(resposta)
    resposta_final = MENSAGEM_SAIDA_BLOQUEADA if bloqueado else resposta

    if bloqueado:
        logger.warning("Saída bloqueada: %s", motivo)

    return {
        "saida_bloqueada": bloqueado,
        "resposta_final": resposta_final,
        "historico": [AIMessage(content=resposta_final)],
    }
