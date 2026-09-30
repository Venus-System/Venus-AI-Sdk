"""Integração Google Calendar do Venus — funções importáveis por quem sobe o
backend real do mobile/web. O SDK NUNCA conduz o fluxo OAuth sozinho (não
expõe rota HTTP de início de consentimento nem de callback): quem decide
onde essas rotas moram é o backend do produto, exatamente como quem sobe o
grafo decide `pool`/`checkpointer`/`store` (ver `flows/venus_flow.py`).

Módulo OPCIONAL — depende de `cryptography` (extra `google_calendar`, ver
`pyproject.toml`), nunca importado pelo resto do SDK.

Fluxo esperado, dividido por quem executa cada parte:

1. Backend do mobile/web redireciona o usuário pro consentimento do Google
   (fora deste módulo — página/tela de "Conectar Google Calendar").
2. Google redireciona de volta pro backend com um `code`; o backend chama
   `trocar_codigo_por_token(code, redirect_uri)` daqui.
3. O backend chama `salvar_refresh_token(pool, user_id, resposta["refresh_token"],
   escopo=resposta["scope"])` pra persistir (cifrado) no Postgres do Venus.
4. Dali em diante, `tools/calendario.py` (rodando dentro do agente de
   rotina) chama `obter_refresh_token` + `renovar_access_token` sozinho,
   sem precisar do backend de novo — só na hora de checar disponibilidade.

`trocar_codigo_por_token`/`renovar_access_token` LEVANTAM exceção em falha
de rede/protocolo (`resp.raise_for_status()`) — diferente das tools do
grafo, essas duas são chamadas por código externo ao Venus (o backend do
produto), que deve decidir como tratar o erro (ex.: pedir pro usuário
reconectar). `tools/calendario.py` é quem, por rodar DENTRO de um agente
ReAct, precisa capturar essas exceções e devolver resposta estruturada —
nunca este módulo.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
_TIMEOUT_SEGUNDOS = 30


def _fernet() -> Any:
    from cryptography.fernet import Fernet

    chave = os.getenv("GOOGLE_TOKEN_ENCRYPTION_KEY")
    if not chave:
        raise ValueError(
            "GOOGLE_TOKEN_ENCRYPTION_KEY não configurada — necessária pra cifrar/"
            "decifrar o refresh_token do Google Calendar em repouso. Gere uma com "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"`."
        )
    return Fernet(chave.encode())


def cifrar_token(texto: str) -> bytes:
    """Cifra um refresh_token pra gravar em `venus.google_oauth_tokens` —
    nunca gravamos token em texto puro."""
    return _fernet().encrypt(texto.encode())


def decifrar_token(cifrado: bytes) -> str:
    return _fernet().decrypt(bytes(cifrado)).decode()


def _credenciais(client_id: str | None, client_secret: str | None) -> tuple[str, str]:
    client_id = client_id or os.getenv("GOOGLE_CLIENT_ID")
    client_secret = client_secret or os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError(
            "GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET não configurados — necessários "
            "pra falar com o endpoint de token do Google."
        )
    return client_id, client_secret


async def trocar_codigo_por_token(
    code: str, redirect_uri: str, *, client_id: str | None = None,
    client_secret: str | None = None, httpx_client: Any | None = None,
) -> dict:
    """Troca o `code` recebido no callback OAuth por `access_token`/
    `refresh_token`/`scope`. Chamado pelo backend do mobile/web logo depois
    do redirect do Google. Levanta em falha de rede/protocolo (ver docstring
    do módulo) — o chamador decide como tratar."""
    cid, secret = _credenciais(client_id, client_secret)
    dados = {
        "code": code, "client_id": cid, "client_secret": secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    }
    return await _post_token(dados, httpx_client)


async def renovar_access_token(
    refresh_token: str, *, client_id: str | None = None, client_secret: str | None = None,
    httpx_client: Any | None = None,
) -> dict:
    """Troca um `refresh_token` já salvo por um `access_token` novo (curto,
    ~1h de validade) — chamado toda vez que uma tool precisar falar com a
    API do Google Calendar. Levanta em falha de rede/protocolo (inclusive
    refresh_token revogado pelo usuário — `400 invalid_grant`)."""
    cid, secret = _credenciais(client_id, client_secret)
    dados = {
        "refresh_token": refresh_token, "client_id": cid,
        "client_secret": secret, "grant_type": "refresh_token",
    }
    return await _post_token(dados, httpx_client)


async def _post_token(dados: dict[str, str], httpx_client: Any | None) -> dict:
    """POST no endpoint de token do Google; usa `httpx_client` se vier (e não o
    fecha) ou abre um cliente só para esta chamada."""
    cliente = httpx_client or httpx.AsyncClient(timeout=_TIMEOUT_SEGUNDOS)
    try:
        resp = await cliente.post(TOKEN_URL, data=dados)
        resp.raise_for_status()
        return resp.json()
    finally:
        if httpx_client is None:
            await cliente.aclose()


async def salvar_refresh_token(pool: Any, user_id: int, refresh_token: str, *, escopo: str) -> None:
    """Grava (upsert) o refresh_token cifrado do usuário em
    `venus.google_oauth_tokens` — chamado pelo backend do mobile/web logo
    depois de `trocar_codigo_por_token`."""
    cifrado = cifrar_token(refresh_token)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO venus.google_oauth_tokens (fk_user_id, encrypted_refresh_token, scope)
            VALUES ($1, $2, $3)
            ON CONFLICT (fk_user_id) DO UPDATE
                SET encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
                    scope = EXCLUDED.scope,
                    updated_at = now()
            """,
            user_id, cifrado, escopo,
        )


async def obter_refresh_token(pool: Any, user_id: int) -> str | None:
    """Devolve o refresh_token decifrado do usuário, ou `None` se ele nunca
    conectou o Google Calendar. `None` não é erro — `tools/calendario.py`
    trata isso como resposta estruturada ("usuário não conectou"), nunca
    como exceção."""
    async with pool.acquire() as conn:
        linha = await conn.fetchrow(
            "SELECT encrypted_refresh_token FROM venus.google_oauth_tokens WHERE fk_user_id = $1",
            user_id,
        )
    if linha is None:
        return None
    return decifrar_token(linha["encrypted_refresh_token"])


async def remover_refresh_token(pool: Any, user_id: int) -> None:
    """Remove o token salvo — chamado pelo backend quando o usuário
    desconecta o Google Calendar manualmente, ou quando uma renovação falha
    com `invalid_grant` (acesso revogado do lado do Google)."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM venus.google_oauth_tokens WHERE fk_user_id = $1", user_id)
