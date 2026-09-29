"""Testes de `integrations/google_calendar.py` — troca/renovação de token
com o Google (via `httpx.MockTransport`, sem rede), cifra/decifra do
refresh_token (Fernet) e leitura/gravação em `venus.google_oauth_tokens`
(pool `asyncpg` falso, ver `tests/_fakes.py`).

Diferente das tools do grafo, as funções deste módulo são chamadas por
código EXTERNO ao Venus (o backend do mobile/web) — por isso elas devem
LEVANTAR em falha de rede/protocolo, nunca devolver resposta estruturada
(quem trata isso pra dentro do grafo é `tools/calendario.py`, ver
`test_tools_calendario.py`)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from cryptography.fernet import Fernet

from venus_sdk.integrations.google_calendar import (
    TOKEN_URL,
    cifrar_token,
    decifrar_token,
    obter_refresh_token,
    remover_refresh_token,
    renovar_access_token,
    salvar_refresh_token,
    trocar_codigo_por_token,
)
from _fakes import ConexaoFalsa, PoolFalso


def _rodar(coro):
    return asyncio.run(coro)


def _cliente_mock(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- cifrar_token / decifrar_token ---


def test_cifrar_decifrar_token_roundtrip(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

    cifrado = cifrar_token("refresh-token-secreto")

    assert cifrado != b"refresh-token-secreto"  # nunca texto puro
    assert decifrar_token(cifrado) == "refresh-token-secreto"


def test_cifrar_token_sem_chave_configurada_levanta_erro_claro(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_TOKEN_ENCRYPTION_KEY", raising=False)

    with pytest.raises(ValueError, match="GOOGLE_TOKEN_ENCRYPTION_KEY"):
        cifrar_token("x")


# --- trocar_codigo_por_token / renovar_access_token ---


def test_trocar_codigo_por_token_chama_endpoint_com_grant_type_correto() -> None:
    capturado: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        capturado["url"] = str(request.url)
        capturado["corpo"] = dict(x.split("=") for x in request.content.decode().split("&"))
        return httpx.Response(200, json={"access_token": "acc1", "refresh_token": "ref1", "scope": "calendar.freebusy"})

    resposta = _rodar(trocar_codigo_por_token(
        "codigo-abc", "https://app.exemplo/callback",
        client_id="cid", client_secret="csecret", httpx_client=_cliente_mock(handler),
    ))

    assert capturado["url"] == TOKEN_URL
    assert capturado["corpo"]["grant_type"] == "authorization_code"
    assert capturado["corpo"]["code"] == "codigo-abc"
    assert resposta == {"access_token": "acc1", "refresh_token": "ref1", "scope": "calendar.freebusy"}


def test_renovar_access_token_usa_grant_type_refresh_token() -> None:
    capturado: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        capturado["corpo"] = dict(x.split("=") for x in request.content.decode().split("&"))
        return httpx.Response(200, json={"access_token": "acc-novo", "expires_in": 3599})

    resposta = _rodar(renovar_access_token(
        "refresh-salvo", client_id="cid", client_secret="csecret", httpx_client=_cliente_mock(handler),
    ))

    assert capturado["corpo"]["grant_type"] == "refresh_token"
    assert capturado["corpo"]["refresh_token"] == "refresh-salvo"
    assert resposta["access_token"] == "acc-novo"


def test_renovar_access_token_propaga_erro_quando_token_foi_revogado() -> None:
    """`invalid_grant` (usuário revogou o acesso do lado do Google) precisa
    subir como exceção — é o chamador (`tools/calendario.py` ou o backend)
    quem decide tratar, este módulo não engole o erro."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(httpx.HTTPStatusError):
        _rodar(renovar_access_token(
            "refresh-revogado", client_id="cid", client_secret="csecret", httpx_client=_cliente_mock(handler),
        ))


def test_trocar_codigo_por_token_sem_credenciais_levanta_erro_claro(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)

    with pytest.raises(ValueError, match="GOOGLE_CLIENT_ID"):
        _rodar(trocar_codigo_por_token("codigo", "https://app.exemplo/callback"))


# --- salvar_refresh_token / obter_refresh_token / remover_refresh_token ---


def test_salvar_refresh_token_grava_cifrado_nunca_texto_puro(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    conexao = ConexaoFalsa()
    pool = PoolFalso(conexao)

    _rodar(salvar_refresh_token(pool, 42, "refresh-do-usuario-42", escopo="calendar.freebusy"))

    query, args = conexao.chamadas[0]
    assert "INSERT INTO venus.google_oauth_tokens" in query
    assert "ON CONFLICT (fk_user_id) DO UPDATE" in query
    assert args[0] == 42
    assert args[1] != b"refresh-do-usuario-42"  # cifrado, não texto puro
    assert args[2] == "calendar.freebusy"


def test_obter_refresh_token_decifra_o_que_foi_salvo(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    cifrado = cifrar_token("refresh-original")
    pool = PoolFalso(ConexaoFalsa(fetchrow={"refresh_token_cifrado": cifrado}))

    resultado = _rodar(obter_refresh_token(pool, 42))

    assert resultado == "refresh-original"


def test_obter_refresh_token_usuario_nunca_conectou_devolve_none() -> None:
    """Sem linha na tabela = usuário nunca conectou o Google Calendar — não
    é erro, `tools/calendario.py` trata como resposta estruturada."""
    pool = PoolFalso(ConexaoFalsa(fetchrow=None))

    assert _rodar(obter_refresh_token(pool, 999)) is None


def test_remover_refresh_token_executa_delete_do_usuario_certo() -> None:
    conexao = ConexaoFalsa()
    pool = PoolFalso(conexao)

    _rodar(remover_refresh_token(pool, 42))

    query, args = conexao.chamadas[0]
    assert "DELETE FROM venus.google_oauth_tokens" in query
    assert args == (42,)
