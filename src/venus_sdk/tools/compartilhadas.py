"""Tool compartilhada entre produto, ingrediente e rotina
(`venus.user_allergies` + `venus.allergies`)."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from venus_sdk.tools._util import consultar, exigir_pool


_SQL_ALERGIAS_DO_USUARIO = """
    SELECT a.allergy_name, a.allergy_type, ua.severity
    FROM venus.user_allergies ua
    JOIN venus.allergies a ON a.allergy_id = ua.fk_allergy_id
    WHERE ua.fk_user_id = $1
"""
# Marcador interno: `consultar` devolve `nao_encontrado(vazio)` sem linhas, e
# "sem alergia declarada" tem resposta própria (é válida, não erro).
_MARCADOR_SEM_ALERGIAS = "__sem_alergias__"


def montar_tools_compartilhadas(pool: Any) -> list[BaseTool]:
    """Monta a tool `get_user_allergies`, com o `pool` capturado por closure.

    Levanta `ValueError` se `pool` for `None` — só na hora em que o
    especialista tentar de fato usá-la (`nodes/especialistas.py` só chama
    isso lazily, no primeiro uso real do nó), nunca na montagem do grafo.
    """
    exigir_pool(pool, "montar_tools_compartilhadas")

    @tool
    async def get_user_allergies(user_id: int) -> list[dict]:
        """Lista as alergias/sensibilidades que o usuário declarou (nome,
        tipo e severidade) — usada para nunca recomendar produto/ingrediente
        que bata com uma delas."""
        resposta = await consultar(pool, "get_user_allergies", _SQL_ALERGIAS_DO_USUARIO, user_id,
                                   vazio=_MARCADOR_SEM_ALERGIAS)
        if isinstance(resposta, dict) and resposta.get("mensagem") == _MARCADOR_SEM_ALERGIAS:
            return {"encontrado": False, "alergias": [],
                    "mensagem": "o usuário não declarou nenhuma alergia"}
        return resposta

    return [get_user_allergies]
