"""Tools do agente Produto — consulta estruturada ao Postgres (schema
`venus`), via `asyncpg`. Não é RAG (ver nota em `tools/ingrediente.py`).

Colunas/FKs conferidas direto no catálogo do Postgres de teste (`DATABASE_URL`)
em 2026-09-05 — a PK de cada tabela é `<tabela>_id` (`product_id`,
`brand_id`...), não `id` genérico como a documentação resumida sugeria.

Validado manualmente em 2026-09-05 contra o Postgres de teste real (as 4
tools, com dado de verdade — produto com/sem score, com/sem ingrediente
cadastrado). Sem teste automatizado no CI pela mesma razão do checkpointer/
store Mongo (ver `tests/test_tools_produto_ingrediente.py`): evita bater
num serviço externo de verdade a cada execução da suíte.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool


def montar_tools_produto(pool: Any) -> list[BaseTool]:
    """Monta as 4 tools do agente Produto, com o `pool` capturado por closure.

    Levanta `ValueError` se `pool` for `None` — só na hora em que o
    especialista tentar de fato usá-las, nunca na montagem do grafo (ver
    `nodes/especialistas.py::montar_no_agente_produto`).
    """
    if pool is None:
        raise ValueError(
            "montar_tools_produto requer um pool do Postgres (asyncpg) — "
            "quem monta o grafo deve criar o pool e passar via "
            "compilar_grafo_venus(pool=...)."
        )

    @tool
    async def get_product(product_id: int) -> dict:
        """Traz os dados básicos de um produto: nome, descrição, marca e
        categoria. É a consulta mais simples — 'o que é esse produto'."""
        query = """
            SELECT p.name, p.description, p.slug,
                   b.name AS brand_name, pc.name AS category_name
            FROM venus.products p
            JOIN venus.brands b ON b.brand_id = p.fk_brand_id
            JOIN venus.product_categories pc ON pc.product_category_id = p.fk_product_category_id
            WHERE p.product_id = $1
        """
        async with pool.acquire() as conn:
            linha = await conn.fetchrow(query, product_id)
        return dict(linha) if linha else {"erro": "produto não encontrado"}

    @tool
    async def get_product_score(product_id: int) -> dict:
        """Traz a nota geral (objetiva) do produto — a mesma pra qualquer
        pessoa que perguntar, sem levar em conta o perfil de quem pediu.
        Usa sempre a versão atual da fórmula do produto."""
        query = """
            SELECT ps.overall_score, ps.health_score, ps.environmental_score,
                   ps.ethical_score, ps.performance_score,
                   ps.transparency_score, ps.confidence_score
            FROM venus.product_scores ps
            JOIN venus.product_versions pv
                ON pv.product_version_id = ps.fk_product_version_id
            WHERE pv.fk_product_id = $1 AND pv.is_current = true
        """
        async with pool.acquire() as conn:
            linha = await conn.fetchrow(query, product_id)
        return dict(linha) if linha else {"erro": "score não encontrado para este produto"}

    @tool
    async def get_personalized_score(product_id: int, user_id: int) -> dict:
        """Traz a nota calculada especificamente para este usuário sobre
        este produto — o que justifica por que foi recomendado pra ele."""
        query = """
            SELECT ps.final_score, ps.compatibility_percentage, ps.risk_level,
                   ps.recommendation_level, ps.summary
            FROM venus.personalized_scores ps
            JOIN venus.product_versions pv
                ON pv.product_version_id = ps.fk_product_version_id
            WHERE pv.fk_product_id = $1
              AND pv.is_current = true
              AND ps.fk_user_id = $2
            ORDER BY ps.created_at DESC
            LIMIT 1
        """
        async with pool.acquire() as conn:
            linha = await conn.fetchrow(query, product_id, user_id)
        return dict(linha) if linha else {"erro": "score personalizado não encontrado para este usuário/produto"}

    @tool
    async def get_product_ingredients(product_id: int) -> list[dict]:
        """Lista os ingredientes que compõem o produto, na ordem do rótulo
        (versão atual da fórmula)."""
        query = """
            SELECT pi.position, i.common_name, i.inci_name
            FROM venus.product_ingredients pi
            JOIN venus.product_versions pv
                ON pv.product_version_id = pi.fk_product_version_id
            JOIN venus.ingredients i ON i.ingredient_id = pi.fk_ingredient_id
            WHERE pv.fk_product_id = $1 AND pv.is_current = true
            ORDER BY pi.position
        """
        async with pool.acquire() as conn:
            linhas = await conn.fetch(query, product_id)
        return [dict(linha) for linha in linhas]

    return [get_product, get_product_score, get_personalized_score, get_product_ingredients]
