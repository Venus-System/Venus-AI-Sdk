"""Tools do agente Ingrediente — consulta estruturada a um catálogo já
curado (ETL a partir de ANVISA/CosIng/PubChem, materializado no Postgres,
schema `venus`), via `asyncpg`.

Importante: isso NÃO é RAG, mesmo a base tendo origem externa — é consulta
estruturada por ID/termo a tabelas já povoadas. RAG de verdade (o sentido
cobrado pela disciplina) é o `faq_retriever` (ver `tools/faq.py` e `rag/`).

Validado manualmente em 2026-09-05 contra o Postgres de teste real (as 5
tools, com dado de verdade — busca por termo, ingrediente com/sem
regulação). Mesma nota de `tools/produto.py` sobre não ter teste
automatizado no CI.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from venus_sdk.tools._util import LIMITE_BUSCA, consultar, normalizar_termo, radical_de_busca, sem_acento


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
    async def search_ingredient(termo: str) -> list[dict] | dict:
        """Acha o ingrediente a partir do que o usuário digitou — nome
        comum, nome INCI ou um apelido/tradução. É o ponto de entrada:
        devolve os candidatos (id + nomes) pra desambiguar antes de chamar
        as outras tools de ingrediente."""
        termo = normalizar_termo(termo)
        if not termo:
            return {"erro": "informe um nome de ingrediente para buscar"}
        query = f"""
            SELECT DISTINCT i.ingredient_id, i.common_name, i.inci_name
            FROM venus.ingredients i
            LEFT JOIN venus.ingredient_aliases ia
                ON ia.fk_ingredient_id = i.ingredient_id
            WHERE {sem_acento("i.common_name")} ILIKE '%' || $1 || '%'
               OR {sem_acento("i.inci_name")} ILIKE '%' || $1 || '%'
               OR {sem_acento("ia.alias_name")} ILIKE '%' || $1 || '%'
            ORDER BY i.common_name
            LIMIT {LIMITE_BUSCA}
        """
        vazio = "nenhum ingrediente encontrado — não invente um ingredient_id"
        resultado = await consultar(pool, "search_ingredient", query, termo, vazio=vazio)
        if isinstance(resultado, dict) and resultado.get("encontrado") is False:
            # Português x INCI: "niacinamida" não é substring de "NIACINAMIDE".
            # Tenta de novo com o radical (sem as 2 últimas letras).
            radical = radical_de_busca(termo)
            if radical != termo:
                resultado = await consultar(pool, "search_ingredient", query, radical, vazio=vazio)
        return resultado

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
        return await consultar(pool, "get_ingredient_summary", query, ingredient_id,
                               uma_linha=True, vazio="ingrediente não encontrado")

    @tool
    async def get_ingredient_properties(ingredient_id: int) -> list[dict]:
        """Propriedades técnicas/químicas do ingrediente, cada uma com a
        fonte de onde veio (`source_reference`)."""
        query = """
            SELECT property_name, property_value, unit, source_reference
            FROM venus.ingredient_properties
            WHERE fk_ingredient_id = $1
        """
        return await consultar(pool, "get_ingredient_properties", query, ingredient_id,
                               vazio="nenhuma propriedade cadastrada para este ingrediente")

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
        return await consultar(pool, "get_ingredient_effects", query, ingredient_id, profile_tag,
                               vazio="nenhum efeito cadastrado para este ingrediente/perfil")

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
        return await consultar(
            pool, "get_ingredient_regulations", query, ingredient_id,
            vazio="nenhuma restrição regulatória cadastrada para este ingrediente",
        )

    return [
        search_ingredient,
        get_ingredient_summary,
        get_ingredient_properties,
        get_ingredient_effects,
        get_ingredient_regulations,
    ]
