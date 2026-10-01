"""Eventos de rotina na agenda Google do usuário — criar/atualizar e remover.

Cada evento criado pela Venus leva a propriedade privada `venus_rotina`
("manha" ou "noite"): um novo agendamento do mesmo período ATUALIZA o evento
existente em vez de duplicar, e "tira minha rotina da agenda" acha o evento
certo. Quem decide QUANDO gravar é `nodes/agendamento.py` (só depois da
confirmação do usuário); aqui só há a conversa HTTP com o Google.

Levanta `httpx.HTTPError` em falha de rede/API — quem chama trata."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import httpx

EVENTOS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
PROPRIEDADE_ROTINA = "venus_rotina"
FUSO_DO_EVENTO = "America/Sao_Paulo"
_TIMEOUT_SEGUNDOS = 15

NOMES_DOS_PERIODOS = {"manha": "manhã", "noite": "noite"}
# Siglas que o usuário/LLM usa -> código do RRULE (RFC 5545).
DIAS_DA_SEMANA = {"seg": "MO", "ter": "TU", "qua": "WE", "qui": "TH", "sex": "FR", "sab": "SA", "dom": "SU"}
RECORRENCIAS = ("diaria", "dias_uteis", "dias_especificos", "uma_vez")
_DIAS_UTEIS = ("seg", "ter", "qua", "qui", "sex")


def regra_de_recorrencia(recorrencia: str, dias_semana: list[str] | None = None) -> list[str]:
    """`recurrence` do evento Google para a recorrência escolhida ([] = só uma vez)."""
    if recorrencia == "diaria":
        return ["RRULE:FREQ=DAILY"]
    if recorrencia == "uma_vez":
        return []
    dias = _DIAS_UTEIS if recorrencia == "dias_uteis" else (dias_semana or [])
    return [f"RRULE:FREQ=WEEKLY;BYDAY={','.join(DIAS_DA_SEMANA[dia] for dia in dias)}"]


def montar_evento(proposta: dict[str, Any]) -> dict[str, Any]:
    """Corpo do evento Google a partir de uma proposta já validada
    (ver `tools/calendario.py::prepare_routine_schedule`)."""
    inicio = datetime.combine(date.fromisoformat(proposta["data"]), datetime.strptime(proposta["hora"], "%H:%M").time())
    fim = inicio + timedelta(minutes=proposta["duracao_minutos"])
    passos = "\n".join(f"{i}. {nome}" for i, nome in enumerate(proposta["passos"], 1))
    periodo = NOMES_DOS_PERIODOS[proposta["periodo"]]
    return {
        "summary": f"Rotina de skincare/haircare — {periodo} (Venus)",
        "description": f"{passos}\n\nCriado pela Venus a seu pedido. Para mudar, é só pedir no chat.",
        "start": {"dateTime": inicio.isoformat(timespec="seconds"), "timeZone": FUSO_DO_EVENTO},
        "end": {"dateTime": fim.isoformat(timespec="seconds"), "timeZone": FUSO_DO_EVENTO},
        "recurrence": regra_de_recorrencia(proposta["recorrencia"], proposta.get("dias_semana")),
        "extendedProperties": {"private": {PROPRIEDADE_ROTINA: proposta["periodo"]}},
    }


def _cabecalhos(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def buscar_evento_da_rotina(access_token: str, periodo: str, cliente: httpx.AsyncClient) -> dict | None:
    """Evento da rotina desse período criado pela Venus, se existir."""
    resposta = await cliente.get(
        EVENTOS_URL,
        headers=_cabecalhos(access_token),
        params={"privateExtendedProperty": f"{PROPRIEDADE_ROTINA}={periodo}", "maxResults": 1,
                "showDeleted": "false"},
    )
    resposta.raise_for_status()
    itens = resposta.json().get("items") or []
    return itens[0] if itens else None


async def salvar_evento_da_rotina(access_token: str, proposta: dict[str, Any],
                                  cliente: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Cria o evento, ou atualiza o que a Venus já tinha criado para o mesmo
    período. Devolve `{"acao": "criado"|"atualizado", "link": ...}`."""
    proprio = cliente is None
    cliente = cliente or httpx.AsyncClient(timeout=_TIMEOUT_SEGUNDOS)
    try:
        evento = montar_evento(proposta)
        existente = await buscar_evento_da_rotina(access_token, proposta["periodo"], cliente)
        if existente:
            resposta = await cliente.put(f"{EVENTOS_URL}/{existente['id']}", headers=_cabecalhos(access_token),
                                         json=evento)
            acao = "atualizado"
        else:
            resposta = await cliente.post(EVENTOS_URL, headers=_cabecalhos(access_token), json=evento)
            acao = "criado"
        resposta.raise_for_status()
        return {"acao": acao, "link": resposta.json().get("htmlLink")}
    finally:
        if proprio:
            await cliente.aclose()


async def remover_evento_da_rotina(access_token: str, periodo: str,
                                   cliente: httpx.AsyncClient | None = None) -> bool:
    """Remove o evento da rotina desse período. `False` se não havia nenhum."""
    proprio = cliente is None
    cliente = cliente or httpx.AsyncClient(timeout=_TIMEOUT_SEGUNDOS)
    try:
        existente = await buscar_evento_da_rotina(access_token, periodo, cliente)
        if not existente:
            return False
        resposta = await cliente.delete(f"{EVENTOS_URL}/{existente['id']}", headers=_cabecalhos(access_token))
        resposta.raise_for_status()
        return True
    finally:
        if proprio:
            await cliente.aclose()
