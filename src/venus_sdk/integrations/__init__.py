"""Integrações opcionais do Venus com serviços de terceiros que exigem
identidade por usuário (OAuth) — hoje só `google_calendar`.

Diferente de `mcp/` e `a2a_client.py`/`a2a_server.py` (protocolos genéricos,
sem dono de conta), tudo aqui pressupõe um backend externo (mobile/web) que
já conduziu o consentimento do usuário — o SDK nunca inicia OAuth sozinho,
só troca/renova/guarda o token depois que ele já existe.
"""
