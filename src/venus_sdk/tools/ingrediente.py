"""Tools do agente Ingrediente — consulta estruturada a um catálogo já
curado (ETL a partir de ANVISA/CosIng/PubChem, materializado no Postgres,
schema `venus`), via `asyncpg`.

Importante: isso NÃO é RAG, mesmo a base tendo origem externa — é consulta
estruturada por ID/termo a tabelas já povoadas. RAG de verdade (o sentido
cobrado pela disciplina) é o `faq_retriever`, ainda não implementado (ver
`mcp/tools.py`).

Validado manualmente em 2026-09-05 contra o Postgres de teste real (as 5
tools, com dado de verdade — busca por termo, ingrediente com/sem
regulação). Mesma nota de `tools/produto.py` sobre não ter teste
automatizado no CI.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool


def montar_tools_ingrediente(pool: Any) -> list[BaseTool]:
    """Monta as 5 tools do agente Ingrediente, com o `pool` capturado por
    closure. Levanta `ValueError` se `pool` for `None` (só na hora do uso
    real, nunca na montagem do grafo — ver
    `nodes/especialistas.py::montar_no_agente_ingrediente`)."""
    if pool is None:
        raise ValueError(
            "montar_tools_ingrediente requer um pool do Postgres (asyncpg) — "
            "quem monta o grafo deve criar o pool e passar via "
            "compilar_grafo_venus(pool=...)."
        )

    @tool
    async def search_ingredient(termo: str) -> list[dict]:
        """Acha o ingrediente a partir do que o usuário digitou — nome
        comum, nome INCI ou um apelido/tradução. É o ponto de entrada:
        devolve os candidatos (id + nomes) pra desambiguar antes de chamar
        as outras tools de ingrediente."""
        query = """
            SELECT DISTINCT i.ingredient_id, i.common_name, i.inci_name
            FROM venus.ingredients i
            LEFT JOIN venus.ingredient_aliases ia
                ON ia.fk_ingredient_id = i.ingredient_id
            WHERE i.common_name ILIKE '%' || $1 || '%'
               OR i.inci_name ILIKE '%' || $1 || '%'
               OR ia.alias_name ILIKE '%' || $1 || '%'
            LIMIT 10
        """
        async with pool.acquire() as conn:
            linhas = await conn.fetch(query, termo)
        return [dict(linha) for linha in linhas]

    @tool
    async def get_ingredient_summary(ingredient_id: int) -> dict:
        """Explicação geral do ingrediente: pra que serve, é seguro, com
        que confiança científica — a fonte da afirmação vem em
        `source_reference`."""
        query = """
            SELECT function_summary, safety_summary, scientific_confidence,
                   source_reference
            FROM venus.ingredients
            WHERE ingredient_id = $1
        """
        async with pool.acquire() as conn:
            linha = await conn.fetchrow(query, ingredient_id)
        return dict(linha) if linha else {"erro": "ingrediente não encontrado"}

    @tool
    async def get_ingredient_properties(ingredient_id: int) -> list[dict]:
        """Propriedades técnicas/químicas do ingrediente, cada uma com a
        fonte de onde veio (`source_reference`)."""
        query = """
            SELECT property_name, property_value, unit, source_reference
            FROM venus.ingredient_properties
            WHERE fk_ingredient_id = $1
        """
        async with pool.acquire() as conn:
            linhas = await conn.fetch(query, ingredient_id)
        return [dict(linha) for linha in linhas]

    @tool
    async def get_ingredient_effects(ingredient_id: int, profile_tag: str | None = None) -> list[dict]:
        """O que o ingrediente faz na pele/cabelo (hidrata, esfolia, pode
        irritar), opcionalmente filtrado por um perfil (ex.: 'pele
        oleosa'). Sem `profile_tag`, devolve todos os efeitos conhecidos."""
        query = """
            SELECT ie.effect_category, ie.effect_name, ie.effect_description,
                   ie.effect_strength, ie.evidence_level, ie.source_reference,
                   pt.name AS profile_tag
            FROM venus.ingredient_effects ie
            JOIN venus.profile_tags pt ON pt.profile_tag_id = ie.fk_profile_tag_id
            WHERE ie.fk_ingredient_id = $1
              AND ($2::text IS NULL OR pt.name ILIKE $2)
        """
        async with pool.acquire() as conn:
            linhas = await conn.fetch(query, ingredient_id, profile_tag)
        return [dict(linha) for linha in linhas]

    @tool
    async def get_ingredient_regulations(ingredient_id: int) -> list[dict]:
        """Restrições regulatórias do ingrediente — proibição, concentração
        máxima permitida — com o documento oficial por trás
        (`document_url`)."""
        query = """
            SELECT ir.restriction_type, ir.max_concentration_value, ir.unit,
                   ir.notes, r.title, r.country, r.agency, r.document_url
            FROM venus.ingredient_regulations ir
            JOIN venus.regulations r ON r.regulation_id = ir.fk_regulation_id
            WHERE ir.fk_ingredient_id = $1
        """
        async with pool.acquire() as conn:
            linhas = await conn.fetch(query, ingredient_id)
        return [dict(linha) for linha in linhas]

    return [
        search_ingredient,
        get_ingredient_summary,
        get_ingredient_properties,
        get_ingredient_effects,
        get_ingredient_regulations,
    ]
