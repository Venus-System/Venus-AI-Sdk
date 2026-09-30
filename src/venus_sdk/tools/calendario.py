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

import logging
from typing import Any

import httpx
from langchain_core.tools import BaseTool, tool

from venus_sdk.integrations.google_calendar import obter_refresh_token, renovar_access_token
from venus_sdk.tools._util import exigir_pool

logger = logging.getLogger(__name__)

FREEBUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"
_TIMEOUT_SEGUNDOS = 15


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
        try:
            refresh_token = await obter_refresh_token(pool, user_id)
        except Exception as exc:  # noqa: BLE001 — GOOGLE_TOKEN_ENCRYPTION_KEY ausente, driver etc.
            logger.warning("Falha ao buscar token Google do usuário %s: %s", user_id, exc)
            return {"erro": "não consegui checar o Google Calendar agora", "detalhe": type(exc).__name__}

        if refresh_token is None:
            return {"conectado": False}

        try:
            token = await renovar_access_token(refresh_token)
            access_token = token["access_token"]
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

    return [check_availability]
