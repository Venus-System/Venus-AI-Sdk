"""Cópia do Postgres + regras de `data/checkup/` para o grafo do Neo4j.

O Postgres continua sendo a fonte da verdade (só é LIDO aqui) e o Neo4j é
uma cópia refeita a cada execução: tudo o que esta rodada grava leva a marca
`rodada`; no fim, o que ficou com marca antiga (favorito desfeito, produto
removido, regra apagada do CSV) é apagado. Quem consulta durante a cópia vê
os dados anteriores, nunca um grafo vazio.

Rodar: `python scripts/sincronizar_neo4j.py` (toda noite e quando os CSVs
mudarem)."""

from __future__ import annotations

import csv
import logging
import uuid
from pathlib import Path
from typing import Any

from venus_sdk.config.settings import BASE_DIR
from venus_sdk.integrations.grafo_neo4j import ExecutarCypher

logger = logging.getLogger(__name__)

PASTA_REGRAS_PADRAO = BASE_DIR / "data" / "checkup"
TIPO_FILTRO_UV = "Filtro UV"
_TAMANHO_DO_LOTE = 1000
# Tipo de regra no CSV -> tipo de relação no grafo.
RELACOES_DAS_REGRAS = {"conflita_com": "CONFLITA_COM", "precisa_de": "PRECISA_DE", "vem_antes_de": "VEM_ANTES_DE"}
_PERIODOS_VALIDOS = {"", "manha", "noite"}

_CONSTRAINTS = (
    "CREATE CONSTRAINT produto_id IF NOT EXISTS FOR (p:Produto) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT ingrediente_id IF NOT EXISTS FOR (i:Ingrediente) REQUIRE i.id IS UNIQUE",
    "CREATE CONSTRAINT usuario_id IF NOT EXISTS FOR (u:Usuario) REQUIRE u.id IS UNIQUE",
    "CREATE CONSTRAINT tipo_nome IF NOT EXISTS FOR (t:TipoAtivo) REQUIRE t.nome IS UNIQUE",
)

_SQL_PRODUTOS = """
    SELECT p.product_id AS id, p.name AS nome, pc.name AS categoria
    FROM venus.products p
    JOIN venus.product_categories pc ON pc.product_category_id = p.fk_product_category_id
"""
_SQL_INGREDIENTES = "SELECT ingredient_id AS id, inci_name AS inci, common_name AS nome FROM venus.ingredients"
_SQL_COMPOSICAO = """
    SELECT pv.fk_product_id AS produto, pi.fk_ingredient_id AS ingrediente, pi.position AS posicao
    FROM venus.product_ingredients pi
    JOIN venus.product_versions pv ON pv.product_version_id = pi.fk_product_version_id AND pv.is_current
"""
_SQL_FAVORITOS = "SELECT fk_user_id AS usuario, fk_product_id AS produto FROM venus.favorites"
# A categoria "Filtro UV" (e as filhas) existe no banco de QA/produção, mas não
# no schema local de desenvolvimento.
_SQL_FILTROS_UV_DO_CATALOGO = """
    SELECT i.ingredient_id AS id
    FROM venus.ingredients i
    JOIN venus.ingredient_categories c ON c.ingredient_category_id = i.fk_ingredient_category_id
    LEFT JOIN venus.ingredient_categories pai ON pai.ingredient_category_id = c.parent_ingredient_category_id
    WHERE c.name = 'Filtro UV' OR pai.name = 'Filtro UV'
"""

_GRAVAR_PRODUTOS = """
UNWIND $linhas AS l
MERGE (p:Produto {id: l.id}) SET p.nome = l.nome, p.categoria = l.categoria, p.rodada = $rodada
"""
_GRAVAR_INGREDIENTES = """
UNWIND $linhas AS l
MERGE (i:Ingrediente {id: l.id}) SET i.inci = l.inci, i.nome = l.nome, i.rodada = $rodada
"""
_GRAVAR_TIPOS = "UNWIND $linhas AS nome MERGE (t:TipoAtivo {nome: nome}) SET t.rodada = $rodada"
_GRAVAR_USUARIOS = "UNWIND $linhas AS id MERGE (u:Usuario {id: id}) SET u.rodada = $rodada"
_GRAVAR_COMPOSICAO = """
UNWIND $linhas AS l
MATCH (p:Produto {id: l.produto}), (i:Ingrediente {id: l.ingrediente})
MERGE (p)-[c:CONTEM]->(i) SET c.posicao = l.posicao, c.rodada = $rodada
"""
_GRAVAR_FAVORITOS = """
UNWIND $linhas AS l
MATCH (u:Usuario {id: l.usuario}), (p:Produto {id: l.produto})
MERGE (u)-[f:FAVORITOU]->(p) SET f.rodada = $rodada
"""
_GRAVAR_TIPOS_DOS_INGREDIENTES = """
UNWIND $linhas AS l
MATCH (i:Ingrediente {id: l.ingrediente}), (t:TipoAtivo {nome: l.tipo})
MERGE (i)-[e:E_DO_TIPO]->(t) SET e.rodada = $rodada
"""
# O tipo da relação não pode ser parâmetro no Cypher: uma consulta por regra.
_GRAVAR_REGRA = """
UNWIND $linhas AS l
MATCH (a:TipoAtivo {nome: l.tipo}), (b:TipoAtivo {nome: l.outro_tipo})
MERGE (a)-[r:%s]->(b)
SET r.periodo = l.periodo, r.severidade = l.severidade, r.motivo = l.motivo, r.fonte = l.fonte,
    r.revisado = l.revisado, r.rodada = $rodada
"""
_APAGAR_RELACOES_ANTIGAS = """
MATCH (:Produto|Ingrediente|Usuario|TipoAtivo)-[r]->(:Produto|Ingrediente|Usuario|TipoAtivo)
WHERE r.rodada <> $rodada DELETE r
"""
_APAGAR_NOS_ANTIGOS = "MATCH (n:Produto|Ingrediente|Usuario|TipoAtivo) WHERE n.rodada <> $rodada DETACH DELETE n"


def carregar_tipos(arquivo: Path) -> dict[str, str]:
    """`{INCI em maiúsculas: tipo de ativo}` de `tipos_de_ativo.csv`."""
    with open(arquivo, encoding="utf-8", newline="") as entrada:
        return {
            linha["ingrediente_inci"].strip().upper(): linha["tipo"].strip()
            for linha in csv.DictReader(entrada)
            if linha.get("ingrediente_inci", "").strip() and linha.get("tipo", "").strip()
        }


def carregar_regras(arquivo: Path) -> list[dict[str, Any]]:
    """Regras de `regras.csv`, validadas. Levanta `ValueError` apontando a
    linha com problema — melhor parar a cópia do que gravar regra errada."""
    regras = []
    with open(arquivo, encoding="utf-8", newline="") as entrada:
        for numero, linha in enumerate(csv.DictReader(entrada), start=2):
            regra = {chave: (valor or "").strip() for chave, valor in linha.items()}
            if regra.get("regra") not in RELACOES_DAS_REGRAS:
                raise ValueError(f"{arquivo.name}, linha {numero}: regra deve ser uma de {', '.join(RELACOES_DAS_REGRAS)}")
            if not regra.get("tipo") or not regra.get("outro_tipo"):
                raise ValueError(f"{arquivo.name}, linha {numero}: tipo e outro_tipo são obrigatórios")
            if regra.get("periodo", "") not in _PERIODOS_VALIDOS:
                raise ValueError(f"{arquivo.name}, linha {numero}: periodo deve ser vazio, 'manha' ou 'noite'")
            regras.append({
                "tipo": regra["tipo"], "regra": regra["regra"], "outro_tipo": regra["outro_tipo"],
                "periodo": regra.get("periodo") or None, "severidade": regra.get("severidade") or None,
                "motivo": regra.get("motivo") or None, "fonte": regra.get("fonte") or None,
                "revisado": regra.get("revisado", "").lower() == "sim",
            })
    return regras


async def ler_postgres(pool: Any) -> dict[str, list[dict[str, Any]]]:
    """Catálogo, composição e favoritos (só leitura)."""
    async with pool.acquire() as conn:
        dados = {
            nome: [dict(linha) for linha in await conn.fetch(sql)]
            for nome, sql in (("produtos", _SQL_PRODUTOS), ("ingredientes", _SQL_INGREDIENTES),
                              ("composicao", _SQL_COMPOSICAO), ("favoritos", _SQL_FAVORITOS))
        }
        try:
            dados["filtros_uv"] = [dict(linha) for linha in await conn.fetch(_SQL_FILTROS_UV_DO_CATALOGO)]
        except Exception as exc:  # noqa: BLE001 — schema sem ingredient_categories (dev local)
            logger.info("Categoria 'Filtro UV' indisponível no banco (%s); usando só o CSV", type(exc).__name__)
            dados["filtros_uv"] = []
    return dados


def tipos_dos_ingredientes(ingredientes: list[dict[str, Any]], tipos_por_inci: dict[str, str],
                           filtros_uv: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ligações ingrediente -> tipo, do CSV (por INCI) e da categoria do banco."""
    ligacoes = {
        (ingrediente["id"], tipos_por_inci[inci])
        for ingrediente in ingredientes
        if (inci := (ingrediente.get("inci") or "").strip().upper()) in tipos_por_inci
    }
    ligacoes |= {(filtro["id"], TIPO_FILTRO_UV) for filtro in filtros_uv}
    return [{"ingrediente": ingrediente, "tipo": tipo} for ingrediente, tipo in sorted(ligacoes)]


async def sincronizar(pool: Any, executar: ExecutarCypher, pasta: Path = PASTA_REGRAS_PADRAO) -> dict[str, int]:
    """Refaz o grafo e devolve quantos itens de cada tipo foram gravados."""
    tipos_por_inci = carregar_tipos(pasta / "tipos_de_ativo.csv")
    regras = carregar_regras(pasta / "regras.csv")
    dados = await ler_postgres(pool)
    rodada = uuid.uuid4().hex
    nomes_dos_tipos = sorted(set(tipos_por_inci.values()) | {TIPO_FILTRO_UV}
                             | {r["tipo"] for r in regras} | {r["outro_tipo"] for r in regras})
    usuarios = sorted({favorito["usuario"] for favorito in dados["favoritos"]})
    ligacoes = tipos_dos_ingredientes(dados["ingredientes"], tipos_por_inci, dados["filtros_uv"])

    for constraint in _CONSTRAINTS:
        await executar(constraint, {})
    gravacoes = [
        (_GRAVAR_PRODUTOS, dados["produtos"]),
        (_GRAVAR_INGREDIENTES, dados["ingredientes"]),
        (_GRAVAR_TIPOS, nomes_dos_tipos),
        (_GRAVAR_USUARIOS, usuarios),
        (_GRAVAR_COMPOSICAO, dados["composicao"]),
        (_GRAVAR_FAVORITOS, dados["favoritos"]),
        (_GRAVAR_TIPOS_DOS_INGREDIENTES, ligacoes),
    ]
    for regra_csv, relacao in RELACOES_DAS_REGRAS.items():
        gravacoes.append((_GRAVAR_REGRA % relacao, [r for r in regras if r["regra"] == regra_csv]))
    for consulta, linhas in gravacoes:
        for inicio in range(0, len(linhas), _TAMANHO_DO_LOTE):
            await executar(consulta, {"linhas": linhas[inicio : inicio + _TAMANHO_DO_LOTE], "rodada": rodada})

    await executar(_APAGAR_RELACOES_ANTIGAS, {"rodada": rodada})
    await executar(_APAGAR_NOS_ANTIGOS, {"rodada": rodada})
    contagem = {
        "produtos": len(dados["produtos"]), "ingredientes": len(dados["ingredientes"]),
        "composicao": len(dados["composicao"]), "favoritos": len(dados["favoritos"]),
        "tipos_de_ativo": len(nomes_dos_tipos), "ingredientes_com_tipo": len(ligacoes), "regras": len(regras),
    }
    logger.info("Neo4j sincronizado: %s", contagem)
    return contagem
