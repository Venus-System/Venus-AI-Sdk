"""As 4 consultas do check-up da rotina, sobre o grafo do Neo4j.

Todas recebem a rotina JÁ MONTADA pela Venus (`tools/rotina.py`): cada
produto com o período (manhã/noite) e a posição em que ela o colocou. Assim
as consultas olham só o que a pessoa usaria de fato — e o período, que a
Venus calcula pela fórmula, não precisa ser copiado para o grafo.

Modelo do grafo (gravado por `checkup/sincronizar.py`):

    (:Produto {id, nome, categoria})-[:CONTEM]->(:Ingrediente {id, inci})
    (:Ingrediente)-[:E_DO_TIPO]->(:TipoAtivo {nome})
    (:TipoAtivo)-[:CONFLITA_COM {severidade, motivo, fonte}]->(:TipoAtivo)
    (:TipoAtivo)-[:PRECISA_DE {periodo, motivo, fonte}]->(:TipoAtivo)
    (:TipoAtivo)-[:VEM_ANTES_DE {motivo, fonte}]->(:TipoAtivo)
"""

from __future__ import annotations

import asyncio
from typing import Any

from venus_sdk.integrations.grafo_neo4j import ExecutarCypher

# Fração mínima de ingredientes em comum (Jaccard) para dois produtos da mesma
# categoria contarem como repetidos.
SEMELHANCA_MINIMA_REPETIDO = 0.6

# Dois produtos do MESMO período cujos tipos de ativo conflitam. Cada par sai
# uma vez (a.id < b.id); retinoide à noite com ácido de manhã não é conflito.
CONSULTA_CONFLITO = """
UNWIND $produtos AS a
UNWIND $produtos AS b
WITH a, b
WHERE a.id < b.id AND a.periodo = b.periodo
MATCH (pa:Produto {id: a.id})-[:CONTEM]->(:Ingrediente)-[:E_DO_TIPO]->(ta:TipoAtivo)
MATCH (pb:Produto {id: b.id})-[:CONTEM]->(:Ingrediente)-[:E_DO_TIPO]->(tb:TipoAtivo)
MATCH (ta)-[r:CONFLITA_COM]-(tb)
RETURN DISTINCT a.periodo AS periodo,
       pa.nome AS nome_a, ta.nome AS tipo_a,
       pb.nome AS nome_b, tb.nome AS tipo_b,
       r.severidade AS severidade, r.motivo AS motivo, r.fonte AS fonte
"""

# Produto com um ativo que exige outro (ex.: retinoide -> filtro UV) quando
# nenhum produto da rotina NO PERÍODO EXIGIDO tem esse outro ativo — um
# protetor só à noite não cobre o retinoide.
CONSULTA_FALTANDO = """
UNWIND $produtos AS a
MATCH (pa:Produto {id: a.id})-[:CONTEM]->(:Ingrediente)-[:E_DO_TIPO]->(t:TipoAtivo)
      -[r:PRECISA_DE]->(falta:TipoAtivo)
WITH DISTINCT a, pa, t, r, falta
WHERE NOT EXISTS {
  MATCH (pb:Produto)-[:CONTEM]->(:Ingrediente)-[:E_DO_TIPO]->(falta)
  WHERE pb.id IN CASE r.periodo WHEN 'manha' THEN $ids_manha WHEN 'noite' THEN $ids_noite ELSE $ids_todos END
}
RETURN DISTINCT a.periodo AS periodo, pa.nome AS nome, t.nome AS tipo,
       falta.nome AS precisa_de, r.periodo AS periodo_exigido, r.motivo AS motivo, r.fonte AS fonte
"""

# Dois produtos da mesma categoria, no mesmo período, com fórmulas muito
# parecidas (Jaccard dos ingredientes) — sem APOC e sem `id()` (descontinuado).
CONSULTA_REPETIDO = """
UNWIND $produtos AS a
UNWIND $produtos AS b
WITH a, b
WHERE a.id < b.id AND a.periodo = b.periodo
MATCH (pa:Produto {id: a.id}), (pb:Produto {id: b.id})
WHERE pa.categoria = pb.categoria
WITH a, pa, pb,
     [(pa)-[:CONTEM]->(i:Ingrediente) | i.id] AS ia,
     [(pb)-[:CONTEM]->(j:Ingrediente) | j.id] AS ib
WITH a, pa, pb, ia, ib, size([x IN ia WHERE x IN ib]) AS comuns
WITH a, pa, pb, comuns, size(ia) + size(ib) - comuns AS total
WHERE total > 0 AND toFloat(comuns) / total >= $semelhanca_minima
RETURN a.periodo AS periodo, pa.nome AS nome_a, pb.nome AS nome_b, pa.categoria AS categoria,
       round(toFloat(comuns) / total, 2) AS semelhanca
"""

# Produto que deveria vir ANTES de outro (regra entre tipos de ativo) mas está
# depois na ordem que a Venus montou.
CONSULTA_ORDEM = """
UNWIND $produtos AS a
UNWIND $produtos AS b
WITH a, b
WHERE a.periodo = b.periodo AND a.ordem > b.ordem
MATCH (pa:Produto {id: a.id})-[:CONTEM]->(:Ingrediente)-[:E_DO_TIPO]->(ta:TipoAtivo)
      -[r:VEM_ANTES_DE]->(tb:TipoAtivo)<-[:E_DO_TIPO]-(:Ingrediente)<-[:CONTEM]-(pb:Produto {id: b.id})
RETURN DISTINCT a.periodo AS periodo, pa.nome AS aplicar_primeiro, ta.nome AS tipo_primeiro,
       pb.nome AS aplicar_depois, tb.nome AS tipo_depois, r.motivo AS motivo, r.fonte AS fonte
"""


def parametros_da_rotina(passos_por_periodo: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Parâmetros das consultas a partir dos passos de `suggest_routine`
    por período (`{"manha": [...], "noite": [...]}`)."""
    produtos = [
        {"id": passo["product_id"], "periodo": periodo, "ordem": passo["ordem"]}
        for periodo, passos in passos_por_periodo.items()
        for passo in passos
    ]
    ids = {periodo: sorted({p["product_id"] for p in passos}) for periodo, passos in passos_por_periodo.items()}
    return {
        "produtos": produtos,
        "ids_manha": ids.get("manha", []),
        "ids_noite": ids.get("noite", []),
        "ids_todos": sorted({p["id"] for p in produtos}),
        "semelhanca_minima": SEMELHANCA_MINIMA_REPETIDO,
    }


async def checar_rotina(executar: ExecutarCypher, passos_por_periodo: dict[str, list[dict[str, Any]]],
                        periodos: tuple[str, ...]) -> list[dict[str, Any]]:
    """Avisos sobre a rotina, só dos `periodos` pedidos. As 4 consultas rodam
    em paralelo; a rotina do dia inteiro entra sempre (o protetor da manhã
    cobre o retinoide da noite)."""
    parametros = parametros_da_rotina(passos_por_periodo)
    if not parametros["produtos"]:
        return []
    conflitos, faltando, repetidos, ordem = await asyncio.gather(
        executar(CONSULTA_CONFLITO, parametros),
        executar(CONSULTA_FALTANDO, parametros),
        executar(CONSULTA_REPETIDO, parametros),
        executar(CONSULTA_ORDEM, parametros),
    )
    avisos = (
        [_aviso_de_conflito(linha) for linha in conflitos]
        + _avisos_de_faltando(faltando)
        + [_aviso_de_repetido(linha) for linha in repetidos]
        + [_aviso_de_ordem(linha) for linha in ordem]
    )
    return [aviso for aviso in avisos if aviso["periodo"] in periodos]


def _aviso_de_conflito(linha: dict[str, Any]) -> dict[str, Any]:
    return {
        "tipo": "conflito", "periodo": linha["periodo"],
        "produtos": [linha["nome_a"], linha["nome_b"]], "ativos": [linha["tipo_a"], linha["tipo_b"]],
        "severidade": linha.get("severidade"), "motivo": linha.get("motivo"), "fonte": linha.get("fonte"),
    }


def _avisos_de_faltando(linhas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Um aviso por produto e ativo faltando (o mesmo produto pode estar nos
    dois períodos)."""
    vistos: set[tuple[str, str, str]] = set()
    avisos = []
    for linha in linhas:
        chave = (linha["periodo"], linha["nome"], linha["precisa_de"])
        if chave in vistos:
            continue
        vistos.add(chave)
        avisos.append({
            "tipo": "faltando", "periodo": linha["periodo"], "produto": linha["nome"], "ativo": linha["tipo"],
            "precisa_de": linha["precisa_de"], "periodo_exigido": linha.get("periodo_exigido"),
            "motivo": linha.get("motivo"), "fonte": linha.get("fonte"),
        })
    return avisos


def _aviso_de_repetido(linha: dict[str, Any]) -> dict[str, Any]:
    return {
        "tipo": "repetido", "periodo": linha["periodo"], "produtos": [linha["nome_a"], linha["nome_b"]],
        "categoria": linha.get("categoria"), "semelhanca": linha.get("semelhanca"),
    }


def _aviso_de_ordem(linha: dict[str, Any]) -> dict[str, Any]:
    return {
        "tipo": "ordem", "periodo": linha["periodo"],
        "aplicar_primeiro": linha["aplicar_primeiro"], "aplicar_depois": linha["aplicar_depois"],
        "motivo": linha.get("motivo"), "fonte": linha.get("fonte"),
    }
