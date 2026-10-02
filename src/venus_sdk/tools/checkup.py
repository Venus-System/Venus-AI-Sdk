"""Tool do agente Rotina — check-up da rotina no Neo4j (extra `neo4j`).

Só entra no agente se for passada em `compilar_grafo_venus(
tools_rotina_extras=montar_tools_checkup(pool))`. Sem Neo4j configurado, ou
com ele fora do ar, devolve `checado: false` e a rotina segue normal — o
check-up nunca derruba a conversa."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool, tool

from venus_sdk.checkup.consultas import checar_rotina
from venus_sdk.integrations.grafo_neo4j import ExecutarCypher, executor_neo4j, get_neo4j_driver, neo4j_configurado
from venus_sdk.tools._identidade import SEM_USUARIO_IDENTIFICADO, resolver_user_id
from venus_sdk.tools._util import exigir_pool
from venus_sdk.tools.rotina import montar_rotina_do_usuario

logger = logging.getLogger(__name__)

_PERIODOS = {"manha": ("manha",), "noite": ("noite",), "ambos": ("manha", "noite")}
NAO_CHECADO = {"checado": False, "mensagem": "o check-up da rotina não está disponível agora"}


def montar_tools_checkup(pool: Any, executar_cypher: ExecutarCypher | None = None) -> list[BaseTool]:
    """Monta `check_routine_health`. `executar_cypher` permite injetar outro
    executor (testes); por padrão usa o driver de `NEO4J_URI`."""
    exigir_pool(pool, "montar_tools_checkup")

    def _executor() -> ExecutarCypher | None:
        if executar_cypher is not None:
            return executar_cypher
        if not neo4j_configurado():
            return None
        return executor_neo4j(get_neo4j_driver())

    @tool
    async def check_routine_health(user_id: int, periodo: str = "ambos") -> dict:
        """Check-up da rotina do usuário (`periodo`: 'manha', 'noite' ou
        'ambos'): aponta produtos com ativos que conflitam no mesmo período,
        ativos que exigem algo que falta (ex.: retinoide sem protetor solar de
        manhã), ordem de aplicação a ajustar e produtos repetidos. Use DEPOIS
        de montar a rotina com `suggest_routine`. Devolve `avisos` (cada um
        com `motivo`) ou `checado: false` — nesse caso, não comente o
        check-up."""
        periodo = (periodo or "ambos").lower().replace("ã", "a")
        if periodo not in _PERIODOS:
            return {"erro": "periodo deve ser 'manha', 'noite' ou 'ambos'"}
        user_id = resolver_user_id(user_id)
        if user_id is None:
            return SEM_USUARIO_IDENTIFICADO

        executar = _executor()
        if executar is None:
            return NAO_CHECADO
        # O dia inteiro entra sempre: o protetor da manhã cobre o retinoide da noite.
        passos_por_periodo = {}
        for periodo_do_dia in ("manha", "noite"):
            rotina = await montar_rotina_do_usuario(pool, user_id, periodo_do_dia)
            if not isinstance(rotina, dict) or "passos" not in rotina:
                return rotina
            passos_por_periodo[periodo_do_dia] = rotina["passos"]
        try:
            avisos = await checar_rotina(executar, passos_por_periodo, _PERIODOS[periodo])
        except Exception:  # noqa: BLE001 — Neo4j fora do ar/lento, credencial errada etc.
            logger.exception("Falha no check-up da rotina (user %s)", user_id)
            return NAO_CHECADO
        return {"checado": True, "avisos": avisos}

    return [check_routine_health]
