"""Testes de `tools/calendario.py::check_availability` — a tool que o
agente de Rotina usa (quando disponível) pra checar disponibilidade no
Google Calendar antes de fechar um horário (`suggest_routine`).

Ao contrário de `integrations/google_calendar.py` (testado separadamente em
`test_google_calendar_integration.py`), esta tool roda DENTRO de um agente
ReAct — por isso NUNCA pode deixar uma exceção subir; todo caminho (sem
token, falha ao buscar token, falha de rede/API) tem que virar resposta
estruturada, mesmo espírito das outras tools Postgres (`tools/_util.py`) e
da tool A2A (`a2a_client.py::montar_tool_a2a`)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from venus_sdk.tools.calendario import FREEBUSY_URL, montar_tools_calendario

_AsyncClientReal = httpx.AsyncClient  # capturado ANTES de qualquer patch, senão recursão infinita


def _rodar(coro):
    return asyncio.run(coro)


def _cliente_mockado(handler):
    """Fábrica pra substituir `httpx.AsyncClient` nos testes — cada chamada
    devolve um client de verdade, só que plugado num `MockTransport`."""
    return lambda *a, **kw: _AsyncClientReal(transport=httpx.MockTransport(handler))


def _tool():
    return {t.name: t for t in montar_tools_calendario(pool=object())}["check_availability"]


def test_montar_tools_calendario_sem_pool_levanta_erro_claro() -> None:
    with pytest.raises(ValueError, match="pool"):
        montar_tools_calendario(None)


async def _invocar(tool, **kwargs):
    return await tool.ainvoke(kwargs)


def test_check_availability_usuario_nao_conectou_devolve_estruturado_sem_erro() -> None:
    with patch("venus_sdk.tools.calendario.obter_refresh_token", new_callable=AsyncMock, return_value=None):
        resultado = _rodar(_invocar(_tool(), user_id=1, inicio="2026-09-24T07:00:00-03:00",
                                    fim="2026-09-24T08:00:00-03:00"))

    assert resultado == {"conectado": False}


def test_check_availability_falha_ao_buscar_token_nao_derruba() -> None:
    """Ex.: `GOOGLE_TOKEN_ENCRYPTION_KEY` ausente — `obter_refresh_token`
    levanta (indiretamente, via `decifrar_token`); a tool tem que capturar,
    nunca propagar pro agente ReAct."""
    with patch("venus_sdk.tools.calendario.obter_refresh_token", new_callable=AsyncMock,
              side_effect=ValueError("GOOGLE_TOKEN_ENCRYPTION_KEY não configurada")):
        resultado = _rodar(_invocar(_tool(), user_id=1, inicio="2026-09-24T07:00:00-03:00",
                                    fim="2026-09-24T08:00:00-03:00"))

    assert "erro" in resultado
    assert "detalhe" in resultado


def test_check_availability_usuario_livre() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == FREEBUSY_URL
        assert request.headers["authorization"] == "Bearer acc-novo"
        return httpx.Response(200, json={"calendars": {"primary": {"busy": []}}})

    with (
        patch("venus_sdk.tools.calendario.obter_refresh_token", new_callable=AsyncMock, return_value="refresh-42"),
        patch("venus_sdk.tools.calendario.renovar_access_token", new_callable=AsyncMock,
              return_value={"access_token": "acc-novo"}),
        patch("httpx.AsyncClient", _cliente_mockado(handler)),
    ):
        resultado = _rodar(_invocar(_tool(), user_id=42, inicio="2026-09-24T07:00:00-03:00",
                                    fim="2026-09-24T08:00:00-03:00"))

    assert resultado == {"conectado": True, "ocupado": False, "compromissos": []}


def test_check_availability_usuario_ocupado_lista_compromissos() -> None:
    compromisso = {"start": "2026-09-24T07:30:00-03:00", "end": "2026-09-24T08:00:00-03:00"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"calendars": {"primary": {"busy": [compromisso]}}})

    with (
        patch("venus_sdk.tools.calendario.obter_refresh_token", new_callable=AsyncMock, return_value="refresh-42"),
        patch("venus_sdk.tools.calendario.renovar_access_token", new_callable=AsyncMock,
              return_value={"access_token": "acc-novo"}),
        patch("httpx.AsyncClient", _cliente_mockado(handler)),
    ):
        resultado = _rodar(_invocar(_tool(), user_id=42, inicio="2026-09-24T07:00:00-03:00",
                                    fim="2026-09-24T08:00:00-03:00"))

    assert resultado == {"conectado": True, "ocupado": True, "compromissos": [compromisso]}


def test_check_availability_token_revogado_nao_derruba() -> None:
    """`renovar_access_token` levanta `httpx.HTTPStatusError` quando o
    usuário revogou o acesso (`invalid_grant`, ver
    `test_google_calendar_integration.py`) — a tool captura e devolve
    resposta estruturada, nunca deixa subir pro agente ReAct."""
    with (
        patch("venus_sdk.tools.calendario.obter_refresh_token", new_callable=AsyncMock, return_value="refresh-42"),
        patch("venus_sdk.tools.calendario.renovar_access_token", new_callable=AsyncMock,
              side_effect=httpx.HTTPStatusError("invalid_grant", request=None, response=None)),
    ):
        resultado = _rodar(_invocar(_tool(), user_id=42, inicio="2026-09-24T07:00:00-03:00",
                                    fim="2026-09-24T08:00:00-03:00"))

    assert "erro" in resultado


def test_check_availability_falha_na_api_do_freebusy_nao_derruba() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with (
        patch("venus_sdk.tools.calendario.obter_refresh_token", new_callable=AsyncMock, return_value="refresh-42"),
        patch("venus_sdk.tools.calendario.renovar_access_token", new_callable=AsyncMock,
              return_value={"access_token": "acc-expirado"}),
        patch("httpx.AsyncClient", _cliente_mockado(handler)),
    ):
        resultado = _rodar(_invocar(_tool(), user_id=42, inicio="2026-09-24T07:00:00-03:00",
                                    fim="2026-09-24T08:00:00-03:00"))

    assert "erro" in resultado
