"""Confirmação do agendamento na agenda Google — a parte que GRAVA.

O agente de rotina só prepara (`prepare_routine_schedule`/`..._removal`); a
proposta fica em `EstadoVenus.agendamento_pendente` e a resposta pede um
"sim". Na mensagem seguinte, se for uma confirmação, ESTE código executa a
proposta guardada — o LLM nunca grava nada nem decide o que gravar. Qualquer
outra mensagem descarta a proposta."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from venus_sdk.integrations.agenda_rotina import (
    NOMES_DOS_PERIODOS,
    remover_evento_da_rotina,
    salvar_evento_da_rotina,
)
from venus_sdk.integrations.google_calendar import obter_credencial, pode_criar_eventos
from venus_sdk.nodes._evidencias import dados_da_evidencia

logger = logging.getLogger(__name__)

INTENCAO_AGENDAMENTO = "agendamento"
_TOOLS_QUE_PREPARAM = ("prepare_routine_schedule", "prepare_routine_removal")
# Proposta mais velha que isso não vale mais (o usuário pode ter mudado de ideia).
_VALIDADE_SEGUNDOS = 30 * 60
# Confirmação é resposta curta e sem detalhe novo: "sim, mas às 8h" é ajuste,
# não confirmação — volta para o agente preparar de novo.
_TAMANHO_MAXIMO_CONFIRMACAO = 40
_CONFIRMACAO_RE = re.compile(
    r"^\s*(sim|s|pode|pode sim|pode agendar|pode tirar|pode remover|confirmo|confirma|confirmado|ok|okay|"
    r"isso|isso mesmo|claro|quero|bora|manda ver|fechado|beleza|perfeito|agenda|agende)\b[\s!.,]*"
    r"(por favor|pfv|pf|obrigad[oa])?[\s!.]*$",
    re.IGNORECASE,
)
_NEGACAO_RE = re.compile(
    r"^\s*(n[ãa]o|nao quero|não quero|cancela|cancelar|deixa|deixa pra l[áa]|esquece|melhor n[ãa]o|agora n[ãa]o)\b",
    re.IGNORECASE,
)

RESPOSTA_CANCELADO = "Tudo bem, não mexi na sua agenda. Se quiser agendar depois, é só pedir!"


def eh_confirmacao(mensagem: str) -> bool:
    texto = (mensagem or "").strip()
    return len(texto) <= _TAMANHO_MAXIMO_CONFIRMACAO and not re.search(r"\d", texto) and bool(
        _CONFIRMACAO_RE.match(texto)
    )


def eh_negacao(mensagem: str) -> bool:
    return bool(_NEGACAO_RE.match(mensagem or ""))


def propostas_das_evidencias(evidencias: list[dict] | None) -> list[dict[str, Any]]:
    """Propostas que as tools de preparação devolveram neste turno."""
    propostas = []
    for evidencia in evidencias or []:
        if evidencia.get("tool") not in _TOOLS_QUE_PREPARAM:
            continue
        dados = dados_da_evidencia([evidencia], evidencia["tool"])
        if isinstance(dados, dict) and isinstance(dados.get("proposta"), dict):
            propostas.append(dados["proposta"])
    return propostas


def propostas_validas(pendentes: list[dict] | None) -> list[dict[str, Any]]:
    agora = time.time()
    return [p for p in pendentes or [] if agora - float(p.get("criada_em", 0)) <= _VALIDADE_SEGUNDOS]


async def executar_propostas(pool: Any, user_id: int | None, propostas: list[dict[str, Any]]) -> str:
    """Grava (ou remove) na agenda o que o usuário confirmou e devolve o texto
    para ele — sempre o resultado REAL de cada operação."""
    if user_id is None:
        return "Não consegui identificar sua conta para mexer na agenda. Pode tentar de novo pelo app?"
    if not propostas:
        return "Esse pedido de agendamento expirou. Me diz de novo o horário que eu preparo outra vez?"

    from venus_sdk.tools.calendario import _access_token  # evita import circular com as tools

    try:
        credencial = await obter_credencial(pool, user_id)
        if credencial is None or not pode_criar_eventos(credencial[1]):
            return "Para agendar, conecte (ou reconecte) o seu Google Calendar no app e me peça de novo."
        access_token = await _access_token(credencial[0])
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao obter credencial do Google (user %s)", user_id)
        return "Não consegui acessar o seu Google Calendar agora. Pode tentar de novo em instantes?"

    return "\n".join([await _executar_uma(access_token, proposta) for proposta in propostas])


async def _executar_uma(access_token: str, proposta: dict[str, Any]) -> str:
    periodo = NOMES_DOS_PERIODOS.get(proposta.get("periodo"), "")
    try:
        if proposta.get("acao") == "remover":
            removido = await remover_evento_da_rotina(access_token, proposta["periodo"])
            if removido:
                return f"Pronto, tirei a rotina da {periodo} da sua agenda."
            return f"Não encontrei a rotina da {periodo} na sua agenda — nada foi removido."
        resultado = await salvar_evento_da_rotina(access_token, proposta)
    except Exception:  # noqa: BLE001 — rede, token revogado, API do Google
        logger.exception("Falha ao gravar a rotina da %s no Google Calendar", periodo)
        return f"Não consegui mexer na rotina da {periodo} na sua agenda agora. Pode tentar de novo em instantes?"
    verbo = "atualizei" if resultado["acao"] == "atualizado" else "agendei"
    return f"Pronto, {verbo} a rotina da {periodo} na sua agenda, às {proposta['hora']}."
