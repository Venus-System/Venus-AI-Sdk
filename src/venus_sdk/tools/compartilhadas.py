"""Tools compartilhadas entre produto, ingrediente e rotina: alergias
(`venus.user_allergies` + `venus.allergies`) e perfil do usuário
(`venus.user_profiles` + `venus.users` + `venus.user_preferences`)."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool, tool

from venus_sdk.tools._identidade import SEM_USUARIO_IDENTIFICADO, resolver_user_id
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

# `to_jsonb(...)` em vez de colunas fixas: o schema do perfil evolui (a coluna
# `hair_type` já sumiu do banco real) e uma coluna a menos não pode derrubar a
# tool. As preferências ("Valores" no app: vegano, cruelty free...) vêm de
# `user_preferences`; quem nunca marcou nenhuma recebe `{}`.
_SQL_PERFIL_DO_USUARIO = """
    SELECT ((to_jsonb(up) - 'user_profile_id' - 'fk_user_id' - 'is_active' - 'created_at'
             - 'updated_at')
            || jsonb_build_object(
                   'name', u.name,
                   'preferences', COALESCE(
                       (SELECT to_jsonb(pf) - 'user_preference_id' - 'fk_user_id' - 'created_at' - 'updated_at'
                        FROM venus.user_preferences pf
                        WHERE pf.fk_user_id = up.fk_user_id
                        ORDER BY pf.updated_at DESC
                        LIMIT 1),
                       '{}'::jsonb)))::text AS perfil,
           COALESCE((SELECT array_agg(pt.name ORDER BY pt.name)
                     FROM venus.user_profile_tags upt
                     JOIN venus.profile_tags pt ON pt.profile_tag_id = upt.fk_profile_tag_id
                     WHERE upt.fk_user_id = up.fk_user_id AND upt.is_active), '{}') AS tags
    FROM venus.user_profiles up
    JOIN venus.users u ON u.user_id = up.fk_user_id
    WHERE up.fk_user_id = $1 AND up.is_active
"""


def montar_tools_compartilhadas(pool: Any) -> list[BaseTool]:
    """Monta `get_user_allergies` e `get_user_profile`, com o `pool` capturado
    por closure.

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
        user_id = resolver_user_id(user_id)
        if user_id is None:
            return SEM_USUARIO_IDENTIFICADO
        resposta = await consultar(pool, "get_user_allergies", _SQL_ALERGIAS_DO_USUARIO, user_id,
                                   vazio=_MARCADOR_SEM_ALERGIAS)
        if isinstance(resposta, dict) and resposta.get("mensagem") == _MARCADOR_SEM_ALERGIAS:
            return {"encontrado": False, "alergias": [],
                    "mensagem": "o usuário não declarou nenhuma alergia"}
        return resposta

    @tool
    async def get_user_profile(user_id: int) -> dict:
        """Traz o perfil que o usuário preencheu no app: nome do cadastro, faixa etária, gênero, tipo de
        pele, fototipo, sensibilidade, tipo de cabelo, couro cabeludo, gestação/amamentação, condições de
        pele (acne, rosácea, eczema, hiperpigmentação, melasma), preferências de valores (`preferences`:
        vegano, cruelty free, sem fragrância...) e tags de perfil. Alergias ficam em `get_user_allergies`."""
        user_id = resolver_user_id(user_id)
        if user_id is None:
            return SEM_USUARIO_IDENTIFICADO
        resultado = await consultar(pool, "get_user_profile", _SQL_PERFIL_DO_USUARIO, user_id, uma_linha=True,
                                    vazio="o usuário não tem perfil cadastrado")
        if isinstance(resultado, dict) and "perfil" in resultado:
            return {**json.loads(resultado["perfil"]), "tags": resultado["tags"]}
        return resultado

    return [get_user_allergies, get_user_profile]
