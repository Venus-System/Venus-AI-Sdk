"""Conexão com o Neo4j do check-up da rotina (extra `neo4j`).

O Neo4j é uma CÓPIA, só de leitura para a Venus, do catálogo e dos favoritos
do Postgres, mais as regras de `venus_sdk/data/checkup/` (ver `checkup/sincronizar.py`).
O driver é criado sob demanda, uma vez por processo; nada aqui roda no import.

As consultas recebem um `ExecutarCypher` (função `consulta, parametros ->
linhas`) em vez do driver: em teste, troca-se por uma função falsa."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any

from venus_sdk.config.settings import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER

ExecutarCypher = Callable[[str, dict[str, Any]], Awaitable[list[dict[str, Any]]]]

# Curto de propósito: Neo4j lento/fora do ar não pode prender a conversa — o
# check-up é opcional e a rotina segue sem ele.
_TIMEOUT_SEGUNDOS = 5


def neo4j_configurado() -> bool:
    return bool(NEO4J_URI)


@lru_cache(maxsize=1)
def get_neo4j_driver() -> Any:
    """Driver assíncrono do Neo4j para `NEO4J_URI` (+ usuário e senha)."""
    if not NEO4J_URI:
        raise ValueError("NEO4J_URI não configurada — defina no .env para usar o check-up da rotina.")
    from neo4j import AsyncGraphDatabase

    return AsyncGraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
        connection_timeout=_TIMEOUT_SEGUNDOS,
        connection_acquisition_timeout=_TIMEOUT_SEGUNDOS,
    )


def executor_neo4j(driver: Any) -> ExecutarCypher:
    """`ExecutarCypher` que roda a consulta numa sessão do `driver`."""

    async def executar(consulta: str, parametros: dict[str, Any]) -> list[dict[str, Any]]:
        async with driver.session() as sessao:
            resultado = await sessao.run(consulta, parametros)
            return await resultado.data()

    return executar
