"""Utilitário LOCAL de teste — NÃO é o fluxo de produção (esse é do backend
do mobile/web, fora deste repositório, ver `integrations/google_calendar.py`).
Serve só pra gerar o primeiro `refresh_token` de um usuário seed, sem
precisar subir um backend inteiro só pra testar `check_availability`.

    docker compose up -d && python scripts/init_db.py --sem-seed  # aplica a migration nova
    python scripts/conectar_google_calendar.py --user-id 1

Abre o navegador pro consentimento do Google, recebe o `code` num servidor
HTTP local de uso único, troca por token e salva (cifrado) em
`venus.google_oauth_tokens` pro `user_id` indicado — depois disso,
`check_availability` já funciona pra esse usuário local ou via
`VENUS_USE_GOOGLE_CALENDAR=1 python examples/conversar_com_venus.py`.

Exige GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_TOKEN_ENCRYPTION_KEY e
DATABASE_URL no `.env`. O redirect_uri usado aqui (`http://localhost:8765/callback`
por padrão) precisa estar cadastrado nas "URIs de redirecionamento
autorizados" do client OAuth no Google Cloud Console."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import asyncpg

from venus_sdk.config.settings import DATABASE_URL  # também carrega o .env
from venus_sdk.integrations.google_calendar import ESCOPOS_VENUS, salvar_refresh_token, trocar_codigo_por_token

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
ESCOPO = ESCOPOS_VENUS


def _capturar_code(porta: int) -> str:
    """Sobe um servidor HTTP local, de uso único, só pra receber o redirect
    do Google com o `?code=...` — encerra sozinho assim que recebe."""
    resultado: dict[str, str] = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — nome exigido pelo BaseHTTPRequestHandler
            qs = parse_qs(urlparse(self.path).query)
            resultado["code"] = qs.get("code", [""])[0]
            resultado["erro"] = qs.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Erro no consentimento — pode fechar esta aba." if resultado["erro"] else \
                  "Conectado! Pode fechar esta aba e voltar pro terminal."
            self.wfile.write(f"<html><body><p>{msg}</p></body></html>".encode())

        def log_message(self, *args: object) -> None:  # silencia log de acesso no stdout
            pass

    servidor = HTTPServer(("localhost", porta), _Handler)
    servidor.handle_request()  # bloqueia até UMA requisição chegar
    servidor.server_close()
    if resultado.get("erro"):
        raise RuntimeError(f"Google devolveu erro no consentimento: {resultado['erro']}")
    if not resultado.get("code"):
        raise RuntimeError("Não recebi o parâmetro 'code' no redirect.")
    return resultado["code"]


async def main(user_id: int, redirect_uri: str) -> int:
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        print("Defina GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET no .env antes de rodar.")
        return 1
    if not DATABASE_URL:
        print("Defina DATABASE_URL no .env antes de rodar.")
        return 1

    porta = int(urlparse(redirect_uri).port or 80)
    url_consentimento = AUTH_URL + "?" + urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": ESCOPO,
        "access_type": "offline",  # sem isso o Google não devolve refresh_token
        "prompt": "consent",       # força devolver refresh_token mesmo se já consentiu antes
    })

    print(f"Abrindo o navegador pro consentimento do usuário {user_id}...")
    print(f"Se não abrir sozinho, acesse: {url_consentimento}\n")
    webbrowser.open(url_consentimento)

    code = _capturar_code(porta)
    print("Code recebido, trocando por token...")

    token = await trocar_codigo_por_token(code, redirect_uri)
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=1)
    try:
        await salvar_refresh_token(pool, user_id, token["refresh_token"], escopo=token.get("scope", ESCOPO))
    finally:
        await pool.close()

    print(f"Google Calendar conectado pro usuário {user_id}. check_availability já pode ser usada.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--user-id", type=int, required=True, help="usuario_id_postgres (ex.: 1 ou 2, seed)")
    ap.add_argument("--redirect-uri", default="http://localhost:8765/callback",
                    help="precisa estar cadastrado no client OAuth (padrão: http://localhost:8765/callback)")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.user_id, args.redirect_uri)))
