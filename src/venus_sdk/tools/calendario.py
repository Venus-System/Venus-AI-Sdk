"""Tool do agente Rotina — checagem de disponibilidade no Google Calendar do
usuário (ver `integrations/google_calendar.py` pro fluxo de OAuth em si; o
consentimento em si acontece fora do SDK, no backend do mobile/web).

Só entra na lista de tools do agente de rotina se for passada via
`tools_extras` — ver `nodes/especialistas.py::montar_no_agente_rotina` e
`flows/venus_flow.py::compilar_grafo_venus(tools_rotina_extras=...)`. Sem
isso (o caso hoje, por padrão), o agente de rotina funciona exatamente como
antes, sem saber que essa tool existe.

Módulo OPCIONAL — depende de `cryptography` por baixo (via
`integrations/google_calendar.py`, extra `google_calendar`)."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from langchain_core.tools import BaseTool, tool

from venus_sdk.integrations.agenda_rotina import (
    DIAS_DA_SEMANA,
    NOMES_DOS_PERIODOS,
    RECORRENCIAS,
    buscar_evento_da_rotina,
)
from venus_sdk.integrations.google_calendar import (
    obter_credencial,
    obter_refresh_token,
    pode_criar_eventos,
    renovar_access_token,
)
from venus_sdk.tools._identidade import SEM_USUARIO_IDENTIFICADO, resolver_user_id
from venus_sdk.tools._util import exigir_pool
from venus_sdk.tools.rotina import montar_rotina_do_usuario

logger = logging.getLogger(__name__)

FREEBUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"
_TIMEOUT_SEGUNDOS = 15
# Renova um pouco antes de expirar (o Google devolve `expires_in`, ~3600 s).
_MARGEM_EXPIRACAO_SEGUNDOS = 60
_VALIDADE_PADRAO_SEGUNDOS = 3600

# access_token por refresh_token (hash), por processo: sem isso, cada checagem
# gastava uma renovação no Google.
_access_tokens: dict[str, tuple[str, float]] = {}

# Agendamento: duração padrão do evento e o fuso dos horários que o usuário diz.
_DURACAO_PADRAO_MINUTOS = 15
_DURACAO_MAXIMA_MINUTOS = 120
_FUSO_BRASILIA = timezone(timedelta(hours=-3))
_HORA_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_NOMES_DAS_RECORRENCIAS = {
    "diaria": "todo dia", "dias_uteis": "de segunda a sexta", "uma_vez": "só uma vez",
}
_NOMES_DOS_DIAS = {"seg": "seg", "ter": "ter", "qua": "qua", "qui": "qui", "sex": "sex", "sab": "sáb", "dom": "dom"}
PRECISA_RECONECTAR = {
    "conectado": True,
    "precisa_reconectar": True,
    "mensagem": "a conexão com o Google Calendar só permite consultar horários; para agendar, o usuário "
                "precisa reconectar a agenda no app",
}


async def _access_token(refresh_token: str) -> str:
    chave = hashlib.sha256(refresh_token.encode()).hexdigest()
    guardado = _access_tokens.get(chave)
    if guardado and guardado[1] > time.monotonic():
        return guardado[0]
    token = await renovar_access_token(refresh_token)
    validade = float(token.get("expires_in") or _VALIDADE_PADRAO_SEGUNDOS) - _MARGEM_EXPIRACAO_SEGUNDOS
    _access_tokens[chave] = (token["access_token"], time.monotonic() + validade)
    return token["access_token"]


def montar_tools_calendario(pool: Any) -> list[BaseTool]:
    """Monta a tool `check_availability`, com o `pool` capturado por
    closure. Levanta `ValueError` se `pool` for `None` — só no primeiro uso
    real do nó, mesmo padrão das outras fábricas de tool (ver
    `tools/rotina.py`)."""
    exigir_pool(pool, "montar_tools_calendario")

    @tool
    async def check_availability(user_id: int, inicio: str, fim: str) -> dict:
        """Checa se o usuário tem compromisso no Google Calendar entre
        `inicio` e `fim` (ISO 8601 com timezone, ex.
        '2026-09-24T07:00:00-03:00') — use ANTES de fechar um horário de
        rotina com `suggest_routine`, nunca depois. Devolve
        `{"conectado": False}` se o usuário nunca conectou o Google
        Calendar (não é erro — nesse caso, siga sem checar disponibilidade);
        `{"conectado": True, "ocupado": bool, "compromissos": [...]}`
        quando conseguir checar; `{"erro": ...}` em falha de rede/token —
        também não impede seguir, só sem essa informação."""
        user_id = resolver_user_id(user_id)
        if user_id is None:
            return SEM_USUARIO_IDENTIFICADO
        try:
            refresh_token = await obter_refresh_token(pool, user_id)
        except Exception as exc:  # noqa: BLE001 — GOOGLE_TOKEN_ENCRYPTION_KEY ausente, driver etc.
            logger.warning("Falha ao buscar token Google do usuário %s: %s", user_id, exc)
            return {"erro": "não consegui checar o Google Calendar agora", "detalhe": type(exc).__name__}

        if refresh_token is None:
            return {"conectado": False}

        try:
            access_token = await _access_token(refresh_token)
            async with httpx.AsyncClient(timeout=_TIMEOUT_SEGUNDOS) as cliente:
                resp = await cliente.post(
                    FREEBUSY_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={"timeMin": inicio, "timeMax": fim, "items": [{"id": "primary"}]},
                )
                resp.raise_for_status()
                dados = resp.json()
        except Exception as exc:  # noqa: BLE001 — refresh_token revogado, rede fora, API mudou etc.
            logger.warning("Falha ao consultar disponibilidade no Google Calendar (user %s): %s", user_id, exc)
            return {"erro": "não consegui consultar o Google Calendar agora", "detalhe": type(exc).__name__}

        compromissos = dados.get("calendars", {}).get("primary", {}).get("busy", [])
        return {"conectado": True, "ocupado": bool(compromissos), "compromissos": compromissos}

    async def _credencial_para_agendar(user_id: int) -> tuple[str, None] | tuple[None, dict]:
        """`(access_token, None)` ou `(None, resposta de erro para a tool)`."""
        try:
            credencial = await obter_credencial(pool, user_id)
        except Exception as exc:  # noqa: BLE001 — chave de cifra ausente, driver etc.
            logger.warning("Falha ao buscar token Google do usuário %s: %s", user_id, exc)
            return None, {"erro": "não consegui acessar o Google Calendar agora", "detalhe": type(exc).__name__}
        if credencial is None:
            return None, {"conectado": False, "mensagem": "o usuário não conectou o Google Calendar no app"}
        refresh_token, escopo = credencial
        if not pode_criar_eventos(escopo):
            return None, PRECISA_RECONECTAR
        try:
            return await _access_token(refresh_token), None
        except Exception as exc:  # noqa: BLE001 — refresh_token revogado, rede fora
            logger.warning("Falha ao renovar token Google do usuário %s: %s", user_id, exc)
            return None, {"erro": "não consegui acessar o Google Calendar agora", "detalhe": type(exc).__name__}

    @tool
    async def prepare_routine_schedule(
        user_id: int,
        periodo: str,
        hora: str,
        recorrencia: str,
        dias_semana: list[str] | None = None,
        data_inicio: str | None = None,
        duracao_minutos: int = _DURACAO_PADRAO_MINUTOS,
    ) -> dict:
        """PREPARA (não grava) o agendamento da rotina na agenda Google do
        usuário e devolve a `proposta` para ele confirmar. `periodo`: 'manha'
        ou 'noite' (para os dois, chame uma vez para cada). `hora`: 'HH:MM'
        (horário de Brasília). `recorrencia`: 'diaria', 'dias_uteis',
        'dias_especificos' (com `dias_semana`, ex. ['seg','qua','sex']) ou
        'uma_vez'. `data_inicio`: 'AAAA-MM-DD' (padrão: hoje, ou amanhã se o
        horário já passou). Os passos vêm da rotina real do usuário. Só chame
        quando o usuário pedir para AGENDAR e já tiver dito horário e
        recorrência — nunca invente esses dois."""
        user_id = resolver_user_id(user_id)
        if user_id is None:
            return SEM_USUARIO_IDENTIFICADO
        invalido = _validar_agendamento(periodo, hora, recorrencia, dias_semana, data_inicio, duracao_minutos)
        if invalido:
            return {"erro": invalido}
        access_token, falha = await _credencial_para_agendar(user_id)
        if falha:
            return falha

        rotina = await montar_rotina_do_usuario(pool, user_id, periodo)
        if not isinstance(rotina, dict) or "passos" not in rotina:
            return rotina
        if not rotina["passos"]:
            return {"erro": "não há produtos dos favoritos para essa rotina; nada a agendar",
                    "sem_produto_para": rotina.get("sem_produto_para")}

        hora = datetime.strptime(hora, "%H:%M").strftime("%H:%M")
        data = data_inicio or _proxima_data(hora)
        proposta = {
            "acao": "agendar",
            "periodo": periodo,
            "hora": hora,
            "recorrencia": recorrencia,
            "dias_semana": dias_semana if recorrencia == "dias_especificos" else None,
            "data": data,
            "duracao_minutos": duracao_minutos,
            "passos": [passo["nome"] for passo in rotina["passos"]],
            "criada_em": time.time(),
        }
        resposta = {"proposta": proposta, "resumo": resumo_da_proposta(proposta),
                    "instrucao": "Mostre o resumo e PEÇA CONFIRMAÇÃO. Nada foi gravado ainda."}
        conflito = await _conflito_no_primeiro_dia(access_token, proposta)
        if conflito:
            resposta["conflito"] = conflito
        return resposta

    @tool
    async def prepare_routine_removal(user_id: int, periodo: str) -> dict:
        """PREPARA (não remove) a retirada da rotina da agenda Google do
        usuário — só eventos de rotina que a Venus criou. `periodo`: 'manha'
        ou 'noite'. Devolve a `proposta` para ele confirmar."""
        user_id = resolver_user_id(user_id)
        if user_id is None:
            return SEM_USUARIO_IDENTIFICADO
        if periodo not in NOMES_DOS_PERIODOS:
            return {"erro": "periodo deve ser 'manha' ou 'noite'"}
        access_token, falha = await _credencial_para_agendar(user_id)
        if falha:
            return falha
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SEGUNDOS) as cliente:
                existente = await buscar_evento_da_rotina(access_token, periodo, cliente)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha ao buscar evento de rotina (user %s): %s", user_id, exc)
            return {"erro": "não consegui consultar o Google Calendar agora", "detalhe": type(exc).__name__}
        if not existente:
            return {"encontrado": False, "mensagem": f"não há rotina da {NOMES_DOS_PERIODOS[periodo]} na agenda"}
        proposta = {"acao": "remover", "periodo": periodo, "criada_em": time.time()}
        return {"proposta": proposta, "resumo": resumo_da_proposta(proposta),
                "instrucao": "PEÇA CONFIRMAÇÃO. Nada foi removido ainda."}

    async def _conflito_no_primeiro_dia(access_token: str, proposta: dict) -> list | None:
        inicio = datetime.combine(date.fromisoformat(proposta["data"]),
                                  datetime.strptime(proposta["hora"], "%H:%M").time(), _FUSO_BRASILIA)
        fim = inicio + timedelta(minutes=proposta["duracao_minutos"])
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SEGUNDOS) as cliente:
                resp = await cliente.post(
                    FREEBUSY_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={"timeMin": inicio.isoformat(), "timeMax": fim.isoformat(), "items": [{"id": "primary"}]},
                )
                resp.raise_for_status()
                return resp.json().get("calendars", {}).get("primary", {}).get("busy") or None
        except Exception:  # noqa: BLE001 — checar conflito é bônus: sem ele, segue
            return None

    return [check_availability, prepare_routine_schedule, prepare_routine_removal]


def _validar_agendamento(periodo: str, hora: str, recorrencia: str, dias_semana: list[str] | None,
                         data_inicio: str | None, duracao_minutos: int) -> str | None:
    """Mensagem do primeiro problema nos parâmetros, ou `None` se estão ok."""
    if periodo not in NOMES_DOS_PERIODOS:
        return "periodo deve ser 'manha' ou 'noite' (para os dois, prepare um de cada vez)"
    if not _HORA_RE.match(hora or ""):
        return "hora deve estar no formato HH:MM (ex.: 07:00)"
    if recorrencia not in RECORRENCIAS:
        return f"recorrencia deve ser uma de {', '.join(RECORRENCIAS)} — se o usuário não disse, pergunte"
    if recorrencia == "dias_especificos" and (not dias_semana or set(dias_semana) - set(DIAS_DA_SEMANA)):
        return f"dias_semana deve listar dias entre {', '.join(DIAS_DA_SEMANA)}"
    if data_inicio:
        try:
            if date.fromisoformat(data_inicio) < datetime.now(_FUSO_BRASILIA).date():
                return "data_inicio não pode estar no passado"
        except ValueError:
            return "data_inicio deve estar no formato AAAA-MM-DD"
    if not 1 <= duracao_minutos <= _DURACAO_MAXIMA_MINUTOS:
        return f"duracao_minutos deve estar entre 1 e {_DURACAO_MAXIMA_MINUTOS}"
    return None


def _proxima_data(hora: str) -> str:
    """Hoje, se o horário ainda não passou em Brasília; senão, amanhã."""
    agora = datetime.now(_FUSO_BRASILIA)
    horario = datetime.strptime(hora, "%H:%M").time()
    dia = agora.date() if agora.time() < horario else agora.date() + timedelta(days=1)
    return dia.isoformat()


def resumo_da_proposta(proposta: dict) -> str:
    """Texto que o usuário confirma — gerado do que será gravado de fato."""
    periodo = NOMES_DOS_PERIODOS[proposta["periodo"]]
    if proposta["acao"] == "remover":
        return f"Tirar da sua agenda a rotina da {periodo}."
    if proposta["recorrencia"] == "dias_especificos":
        quando = "toda " + ", ".join(_NOMES_DOS_DIAS[dia] for dia in proposta["dias_semana"])
    else:
        quando = _NOMES_DAS_RECORRENCIAS[proposta["recorrencia"]]
    inicio = date.fromisoformat(proposta["data"]).strftime("%d/%m")
    passos = "; ".join(proposta["passos"])
    return (f"Rotina da {periodo} às {proposta['hora']}, {quando}, a partir de {inicio} "
            f"({proposta['duracao_minutos']} min): {passos}.")
