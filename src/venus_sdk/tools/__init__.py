"""Tools Postgres (via `asyncpg`) usadas pelos agentes especialistas.

Cada `montar_tools_*(pool)` recebe um `asyncpg.Pool` já pronto (criado e
gerenciado por quem monta o grafo — nunca pelo SDK, ver `nodes/especialistas.py`)
e devolve a lista de tools `@tool` do LangChain, já com o pool capturado por
closure. Consultam o schema `venus` do Postgres.
"""
