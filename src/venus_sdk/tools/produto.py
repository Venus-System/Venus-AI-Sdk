"""Tools do agente Produto — consulta estruturada ao Postgres (schema
`venus`), via `asyncpg`. Não é RAG (ver nota em `tools/ingrediente.py`).

Colunas/FKs conferidas direto no catálogo do Postgres de teste (`DATABASE_URL`)
em 2026-09-05 — a PK de cada tabela é `<tabela>_id` (`product_id`,
`brand_id`...), não `id` genérico como a documentação resumida sugeria.

Validado manualmente em 2026-09-05 contra o Postgres de teste real (as 4
tools originais, com dado de verdade — produto com/sem score, com/sem
ingrediente cadastrado). Sem teste automatizado no CI pela mesma razão do
checkpointer/store Mongo (ver `tests/test_tools_produto_ingrediente.py`):
evita bater num serviço externo de verdade a cada execução da suíte.

`search_product` foi adicionada em 2026-09-10 (não fazia parte da validação
manual acima) — sem ela, uma pergunta que só cita o NOME do produto (sem
`product_id`) não tinha como ser resolvida: o especialista tinha que
adivinhar o id, o que gerou uma alucinação confirmada ao vivo (produto sem
ingrediente/score cadastrado, mas a resposta "inventou" ingredientes) — ver
`docs/architecture.md`.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from venus_sdk.tools._util import LIMITE_BUSCA, consultar, nao_encontrado, palavras_de_busca, sem_acento


def montar_tools_produto(pool: Any) -> list[BaseTool]:
    """Monta as 5 tools do agente Produto, com o `pool` capturado por closure.

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
    async def search_product(termo: str) -> list[dict] | dict:
        """Acha candidatos a produto a partir de palavras do nome, da marca, da
        categoria ou da descrição (ex.: 'shampoo cacheado', 'sérum vitamina c') — ponto de entrada quando ainda não se
        sabe o `product_id` (mesmo papel do `search_ingredient` do agente de
        Ingrediente). Use isto ANTES de qualquer outra tool de produto quando
        a pergunta só citar um nome; nunca invente um `product_id`."""
        palavras = palavras_de_busca(termo)
        if not palavras:
            return {"erro": "informe um nome, marca ou tipo de produto para buscar"}
        # Casa QUALQUER palavra com nome, marca, categoria ou descrição e
        # ordena por quantas palavras bateram — "produto bom pra cabelo
        # cacheado" acha shampoo/condicionador mesmo sem o nome exato.
        query = f"""
            SELECT p.product_id, p.name, b.name AS brand_name, pc.name AS category_name,
                   count(DISTINCT tok) AS relevancia,
                   EXISTS (SELECT 1 FROM venus.product_versions pv
                           JOIN venus.product_scores ps ON ps.fk_product_version_id = pv.product_version_id
                           WHERE pv.fk_product_id = p.product_id AND pv.is_current) AS tem_score,
                   EXISTS (SELECT 1 FROM venus.product_versions pv
                           JOIN venus.product_ingredients pi ON pi.fk_product_version_id = pv.product_version_id
                           WHERE pv.fk_product_id = p.product_id AND pv.is_current) AS tem_ingredientes
            FROM venus.products p
            JOIN venus.brands b ON b.brand_id = p.fk_brand_id
            JOIN venus.product_categories pc ON pc.product_category_id = p.fk_product_category_id
            JOIN unnest($1::text[]) AS tok ON (
                   {sem_acento("p.name")} ILIKE '%' || tok || '%'
                OR {sem_acento("b.name")} ILIKE '%' || tok || '%'
                OR {sem_acento("pc.name")} ILIKE '%' || tok || '%'
                OR {sem_acento("COALESCE(p.description, '')")} ILIKE '%' || tok || '%'
            )
            GROUP BY p.product_id, p.name, b.name, pc.name
            ORDER BY relevancia DESC, tem_score DESC, tem_ingredientes DESC, p.name
            LIMIT {LIMITE_BUSCA}
        """
        return await consultar(
            pool, "search_product", query, palavras,
            vazio="nenhum produto encontrado com esses termos — não invente um product_id; "
                  "peça ao usuário o nome ou a marca",
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
        return await consultar(pool, "get_product", query, product_id, uma_linha=True,
                               vazio="produto não encontrado")

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
        r = await consultar(pool, "get_product_score", query, product_id, uma_linha=True,
                            vazio="score não encontrado para este produto")
        notas = [v for k, v in r.items() if k.endswith("_score")] if isinstance(r, dict) else []
        if notas and all((n or 0) == 0 for n in notas):
            # Linha existe mas toda zerada = score ainda não calculado (dado
            # real do catálogo); apresentar "nota 0" seria alucinar um fato.
            return nao_encontrado("o score deste produto ainda não foi calculado (todas as notas estão zeradas) — não cite notas")
        return r

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
        return await consultar(pool, "get_personalized_score", query, product_id, user_id,
                               uma_linha=True,
                               vazio="score personalizado não encontrado para este usuário/produto")

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
        return await consultar(
            pool, "get_product_ingredients", query, product_id,
            vazio="este produto não tem ingredientes cadastrados — NÃO invente ingredientes",
        )

    return [
        search_product,
        get_product,
        get_product_score,
        get_personalized_score,
        get_product_ingredients,
    ]
