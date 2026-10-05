"""Nós de guardrail de entrada e saída do grafo principal."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage

from venus_sdk.guardrail_rules import (
    MENSAGEM_ENTRADA_BLOQUEADA,
    MENSAGEM_SAIDA_BLOQUEADA,
    anonimizar_entrada,
    guardrail_entrada,
    guardrail_saida,
    remover_emojis,
)
from venus_sdk.llm.models import extrair_texto_resposta, get_llm_guardrail
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


# --- saúde do classificador (por processo) ------------------------------------
# O fail-open é de propósito (o classificador nunca derruba a conversa), mas
# não pode ser silencioso: com a cota de LLM no fim, a camada 2 ficaria
# desligada por horas sem ninguém notar. Por isso contamos chamadas, falhas e
# fail-opens, e um disjuntor para de chamar o LLM por um tempo depois de várias
# falhas seguidas — senão cada mensagem esperaria o timeout do LLM à toa.
_FALHAS_PARA_ABRIR_PADRAO = 5
_PAUSA_SEGUNDOS_PADRAO = 60.0
_agora = time.monotonic  # trocado nos testes
_trava = threading.Lock()
_estado: dict[str, Any] = {}


def _reiniciar_classificador() -> None:
    """Zera contadores e disjuntor (início do processo e testes)."""
    with _trava:
        _estado.update(chamadas=0, falhas=0, fail_opens=0, puladas_pelo_disjuntor=0,
                       falhas_seguidas=0, aberto=False, tentar_de_novo_em=0.0)


_reiniciar_classificador()


def _numero_do_ambiente(nome: str, padrao: float) -> float:
    try:
        return float(os.getenv(nome, "") or padrao)
    except ValueError:
        return padrao


def _falhas_para_abrir() -> int:
    return max(1, int(_numero_do_ambiente("VENUS_GUARDRAIL_LLM_FALHAS_PARA_ABRIR", _FALHAS_PARA_ABRIR_PADRAO)))


def _pausa_segundos() -> float:
    return max(0.0, _numero_do_ambiente("VENUS_GUARDRAIL_LLM_PAUSA_SEGUNDOS", _PAUSA_SEGUNDOS_PADRAO))


def estatisticas_guardrail_llm() -> dict[str, Any]:
    """Contadores do classificador LLM neste processo, para health check:
    chamadas, falhas, fail-opens, mensagens que o disjuntor deixou passar sem
    classificar e o estado do disjuntor."""
    with _trava:
        estatisticas = {chave: _estado[chave] for chave in
                        ("chamadas", "falhas", "fail_opens", "puladas_pelo_disjuntor", "falhas_seguidas")}
        estatisticas["disjuntor_aberto"] = _estado["aberto"]
        estatisticas["disjuntor_tenta_de_novo_em_segundos"] = (
            round(max(0.0, _estado["tentar_de_novo_em"] - _agora()), 1) if _estado["aberto"] else None
        )
    return estatisticas


def _pular_pelo_disjuntor() -> bool:
    """Disjuntor aberto e ainda na pausa: a mensagem passa sem classificar.
    Passada a pausa, a próxima mensagem tenta o LLM de novo."""
    with _trava:
        if _estado["aberto"] and _agora() < _estado["tentar_de_novo_em"]:
            _estado["puladas_pelo_disjuntor"] += 1
            return True
        _estado["chamadas"] += 1
        return False


def _registrar_falha(erro: Exception) -> None:
    with _trava:
        _estado["falhas"] += 1
        _estado["fail_opens"] += 1
        _estado["falhas_seguidas"] += 1
        abriu = not _estado["aberto"] and _estado["falhas_seguidas"] >= _falhas_para_abrir()
        if abriu or _estado["aberto"]:
            # Falha na tentativa depois da pausa também prorroga a pausa.
            _estado["aberto"] = True
            _estado["tentar_de_novo_em"] = _agora() + _pausa_segundos()
        falhas_seguidas = _estado["falhas_seguidas"]
    # Só o tipo da exceção: a mensagem de erro do provedor pode ecoar o texto
    # do usuário, que nunca vai para log.
    logger.warning(
        "Classificador LLM do guardrail falhou (%s); mensagem liberada sem a camada 2.",
        type(erro).__name__,
        extra={"evento": "guardrail_llm_fail_open", "tipo_erro": type(erro).__name__},
    )
    if abriu:
        logger.error(
            "Classificador LLM do guardrail: %d falhas seguidas, pausado por %.0f s (disjuntor aberto).",
            falhas_seguidas, _pausa_segundos(),
            extra={"evento": "guardrail_llm_disjuntor_aberto", "falhas_seguidas": falhas_seguidas},
        )


def _registrar_sucesso() -> None:
    with _trava:
        fechou = _estado["aberto"]
        _estado.update(falhas_seguidas=0, aberto=False, tentar_de_novo_em=0.0)
    if fechou:
        logger.info("Classificador LLM do guardrail voltou a responder (disjuntor fechado).",
                    extra={"evento": "guardrail_llm_disjuntor_fechado"})


def _classificador_llm_ve_injecao(mensagem: str) -> bool:
    """Segunda camada: um LLM barato responde SEGURO/INJECAO para o que a
    regex deixou passar. Tenta o provedor principal e um de outro provedor
    (`get_llm_guardrail`); se os dois falharem, a mensagem passa (fail-open) —
    o classificador nunca derruba a conversa, e os prompts dos agentes
    continuam recusando manipulação. A mensagem vai delimitada, como dado a
    classificar."""
    if _pular_pelo_disjuntor():
        return False
    try:
        resposta = get_llm_guardrail().invoke(
            [("system", GUARDRAIL_LLM_PROMPT), ("human", f"<mensagem>\n{mensagem}\n</mensagem>")]
        )
    except Exception as erro:  # noqa: BLE001 — qualquer falha do LLM vira fail-open contado
        _registrar_falha(erro)
        return False
    _registrar_sucesso()
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
