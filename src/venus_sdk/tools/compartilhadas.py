"""Tool compartilhada entre produto, ingrediente e rotina.

Schema real conferido direto no Postgres de teste (`venus.user_allergies` /
`venus.allergies`) — não a notação comprimida do doc. Validado manualmente
em 2026-09-05 contra dado real; ver nota em `tools/produto.py`."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool


def montar_tools_compartilhadas(pool: Any) -> list[BaseTool]:
    """Monta a tool `get_user_allergies`, com o `pool` capturado por closure.

    Levanta `ValueError` se `pool` for `None` — só na hora em que o
    especialista tentar de fato usá-la (`nodes/especialistas.py` só chama
    isso lazily, no primeiro uso real do nó), nunca na montagem do grafo.
    """
    if pool is None:
        raise ValueError(
            "montar_tools_compartilhadas requer um pool do Postgres (asyncpg) — "
            "quem monta o grafo deve criar o pool e passar via "
            "compilar_grafo_venus(pool=...)."
        )

    @tool
    async def get_user_allergies(user_id: int) -> list[dict]:
        """Lista as alergias/sensibilidades que o usuário declarou (nome,
        tipo e severidade) — usada para nunca recomendar produto/ingrediente
        que bata com uma delas."""
        query = """
            SELECT a.allergy_name, a.allergy_type, ua.severity
            FROM venus.user_allergies ua
            JOIN venus.allergies a ON a.allergy_id = ua.fk_allergy_id
            WHERE ua.fk_user_id = $1
        """
        async with pool.acquire() as conn:
            linhas = await conn.fetch(query, user_id)
        return [dict(linha) for linha in linhas]

    return [get_user_allergies]
