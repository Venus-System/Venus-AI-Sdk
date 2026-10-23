"""Conferência determinística da amarração nota <-> `product_id` feita pelo
especialista de Produto (campo `produtos` do JSON, ver `prompts/produto.py`).

Na conversa real de 2026-10-20 o Juiz LLM esgotava as tentativas na maioria
dos turnos de produto: o especialista consultava vários produtos e citava a
nota de um no outro (ex.: a nota 76 do product_id 1, "CeraVe Loção Facial
Hidratante Noite", como se fosse do CeraVe Creme Hidratante), e o próprio Juiz
errava ao conferir números num RESULTADOS_TOOLS enorme (reprovava
`performance_score: null`). Aqui o código confere o que dá para conferir com
exatidão — id, nome e cada nota declarada contra o retorno das tools — e o
Juiz LLM fica só com o resto."""

from __future__ import annotations

import re
from typing import Any

from venus_sdk.nodes._evidencias import dados_da_evidencia
from venus_sdk.texto import remover_acentos

# Notas que as tools de score devolvem, por tool.
_NOTAS_POR_TOOL = {
    "get_product_score": (
        "overall_score", "health_score", "environmental_score", "ethical_score",
        "performance_score", "transparency_score", "confidence_score",
    ),
    "get_personalized_score": ("final_score", "compatibility_percentage"),
}
_TOOLS_DE_DETALHE = ("get_product", "get_product_ingredients", *_NOTAS_POR_TOOL)

# Números citados como nota no texto: "63/100", "nota 63", "nota geral de 63", "score 63".
_RE_NOTA_CITADA = re.compile(
    r"\b(\d{1,3}(?:[.,]\d+)?)\s*/\s*100\b"
    r"|\b(?:nota|score|pontua[cç][aã]o)\s+(?:geral\s+|final\s+|personalizada\s+)?(?:de\s+|é\s+|e\s+)?(\d{1,3}(?:[.,]\d+)?)\b",
    re.IGNORECASE,
)
_RE_FIM_DE_FRASE = re.compile(r"[.;!?\n]+(?:\s|$)")
_TAMANHO_MINIMO_PALAVRA = 3


def notas_citadas(texto: str) -> list[float]:
    """Números que o texto apresenta como nota/score."""
    return [_numero(a or b) for a, b in _RE_NOTA_CITADA.findall(texto or "")]


def _numero(valor: Any) -> float:
    return float(str(valor).replace(",", "."))


def _palavras(texto: str) -> set[str]:
    texto = remover_acentos(str(texto or "")).lower()
    return {p for p in re.split(r"[^a-z0-9]+", texto) if len(p) >= _TAMANHO_MINIMO_PALAVRA}


def _mesmo_produto(declarado: str, real: str) -> bool:
    """Todas as palavras do nome declarado estão no nome real ("Eucerin
    Hyaluron-Filler" casa com o nome completo; "CeraVe Creme Hidratante" não
    casa com "CeraVe Loção Facial Hidratante Noite")."""
    palavras = _palavras(declarado)
    return bool(palavras) and palavras <= _palavras(real)


def mesmo_nome(um: str, outro: str) -> bool:
    """Mesmas palavras, sem acento nem maiúsculas ("CeraVe Creme Hidratante" =
    "cerave creme hidratante")."""
    palavras = _palavras(um)
    return bool(palavras) and palavras == _palavras(outro)


def nomes_por_id(evidencias: list[dict] | None) -> dict[str, str]:
    """`product_id` -> nome, de todas as buscas e de `get_product`. Um id que só
    aparece numa tool de detalhe com retorno válido (ex.: a nota de um produto
    achado na tentativa anterior) também conta, com nome vazio — existe, mas
    não dá para conferir o nome."""
    nomes: dict[str, str] = {}
    for evidencia in evidencias or []:
        tool = evidencia.get("tool")
        product_id = (evidencia.get("argumentos") or {}).get("product_id")
        if tool in _TOOLS_DE_DETALHE and product_id is not None and _retorno_valido(evidencia):
            nomes.setdefault(str(product_id), "")
        if tool == "search_product":
            resultado = dados_da_evidencia([evidencia], "search_product")
            for produto in resultado if isinstance(resultado, list) else []:
                if isinstance(produto, dict) and produto.get("product_id") is not None:
                    nomes.setdefault(str(produto["product_id"]), str(produto.get("name") or ""))
        elif tool == "get_product":
            produto = dados_da_evidencia([evidencia], "get_product")
            if product_id is not None and isinstance(produto, dict) and produto.get("name"):
                nomes[str(product_id)] = str(produto["name"])
    return nomes


def _retorno_valido(evidencia: dict) -> bool:
    dados = dados_da_evidencia([evidencia], str(evidencia.get("tool")))
    if isinstance(dados, dict):
        return "erro" not in dados and dados.get("encontrado") is not False
    return isinstance(dados, list) and bool(dados)


def _notas_reais(evidencias: list[dict] | None, product_id: Any) -> dict[str, Any]:
    """Campos que as tools de score devolveram para ESSE `product_id` (só as
    consultadas) — notas e também `risk_level`/`recommendation_level`."""
    notas: dict[str, Any] = {}
    for tool in _NOTAS_POR_TOOL:
        dados = dados_da_evidencia(evidencias, tool, product_id=product_id)
        if isinstance(dados, dict) and dados.get("encontrado") is not False:
            notas.update({campo: valor for campo, valor in dados.items() if campo not in ("erro", "mensagem")})
    return notas


def _mesmo_valor(declarado: Any, real: Any) -> bool:
    if declarado is None or real is None:
        return declarado == real
    if _e_numero(declarado) and _e_numero(real):
        return _numero(declarado) == _numero(real)
    return remover_acentos(str(declarado)).strip().lower() == remover_acentos(str(real)).strip().lower()


def ids_buscados(evidencias: list[dict] | None) -> set[str]:
    """`product_id`s que alguma `search_product` devolveu — os únicos ids legítimos
    (o app só manda o NOME do produto em tela, nunca o id)."""
    ids = set()
    for evidencia in evidencias or []:
        if evidencia.get("tool") != "search_product":
            continue
        resultado = dados_da_evidencia([evidencia], "search_product")
        for produto in resultado if isinstance(resultado, list) else []:
            if isinstance(produto, dict) and produto.get("product_id") is not None:
                ids.add(str(produto["product_id"]))
    return ids


def _problemas_do_item(
    item: Any, evidencias: list[dict] | None, nomes: dict[str, str], buscados: set[str]
) -> list[str]:
    if not isinstance(item, dict) or item.get("product_id") is None:
        return [f"item de `produtos` sem product_id: {item!r}"]
    product_id = str(item["product_id"]).strip()
    if product_id not in nomes:
        return [
            f"product_id {product_id} não veio de nenhuma tool nesta tentativa — nunca invente um id; "
            "para citar esse produto, ache o id dele com search_product (ou deixe-o de fora)"
        ]
    if product_id not in buscados:
        # Chatbot real (2026-10-21): o modelo de fallback "varria" get_product(1..6)
        # e apresentava o que achava como os produtos da conversa.
        return [
            f"product_id {product_id} foi consultado sem ter saído de search_product — id chutado; "
            "ache o produto pelo nome com search_product"
        ]
    problemas = []
    nome_real = nomes[product_id]
    if item.get("nome") and nome_real and not _mesmo_produto(item["nome"], nome_real):
        problemas.append(
            f"product_id {product_id} é \"{nome_real}\", não \"{item['nome']}\" — use o id do produto de que está falando"
        )
    reais = _notas_reais(evidencias, product_id)
    notas = item.get("notas") if isinstance(item.get("notas"), dict) else {}
    for campo, valor in notas.items():
        if campo not in reais and _veio_de_um_homonimo(evidencias, nomes, product_id, campo, valor):
            continue
        if campo not in reais:
            problemas.append(
                f"{campo}={valor} do product_id {product_id} não veio de get_product_score/get_personalized_score "
                "chamada para esse id"
            )
        elif not _mesmo_valor(valor, reais[campo]):
            problemas.append(f"{campo} do product_id {product_id} é {reais[campo]} na tool, não {valor}")
    return problemas


def _veio_de_um_homonimo(
    evidencias: list[dict] | None, nomes: dict[str, str], product_id: str, campo: str, valor: Any
) -> bool:
    """O catálogo tem cópias do mesmo produto com outro id (ex.: "CeraVe Creme
    Hidratante", ids 17 e 59). Para o usuário é o mesmo produto: a nota que a tool
    devolveu para a cópia vale (chatbot real, 2026-10-23: o especialista achou o
    59 na busca e citou a nota personalizada do 17, e a resposta foi reprovada)."""
    nome = nomes.get(product_id) or ""
    return any(
        outro_id != product_id and mesmo_nome(outro_nome, nome)
        and _mesmo_valor(valor, _notas_reais(evidencias, outro_id).get(campo, object()))
        for outro_id, outro_nome in nomes.items()
    )


def _problemas_do_texto(texto: str, produtos: list[dict]) -> list[str]:
    """Toda nota citada no texto precisa estar declarada em `produtos`; numa
    frase que nomeia um único produto, a nota tem de ser DESSE produto."""
    notas_por_produto = [
        (str(item.get("nome") or ""), str(item.get("product_id")),
         {_numero(v) for v in (item.get("notas") or {}).values() if _e_numero(v)})
        for item in produtos if isinstance(item, dict)
    ]
    todas = set().union(*(notas for _, _, notas in notas_por_produto)) if notas_por_produto else set()
    problemas = []
    for frase in _RE_FIM_DE_FRASE.split(texto or ""):
        citadas = notas_citadas(frase)
        if not citadas:
            continue
        nomeados = [p for p in notas_por_produto if p[0] and _palavras(p[0]) <= _palavras(frase)]
        for nota in citadas:
            if nota not in todas:
                problemas.append(f"o texto cita a nota {nota:g} sem ela estar em `produtos[].notas` de nenhum product_id")
            elif len(nomeados) == 1 and nota not in nomeados[0][2]:
                nome, product_id, _ = nomeados[0]
                problemas.append(
                    f"a frase \"{frase.strip()[:120]}\" atribui a nota {nota:g} ao {nome} (product_id {product_id}), "
                    "mas essa nota é de outro produto"
                )
    return problemas


def _e_numero(valor: Any) -> bool:
    try:
        _numero(valor)
    except (TypeError, ValueError):
        return False
    return valor is not None


def conferir_amarracao(especialista: dict, evidencias: list[dict] | None) -> tuple[list[str], str | None]:
    """`(problemas, resumo_verificado)`.

    `problemas` vazio + `resumo_verificado` = a amarração bate com as tools
    (o resumo vai ao Juiz LLM, para ele não reconferir números). Sem o campo
    `produtos` e sem nota citada no texto, não há o que conferir: `([], None)`."""
    produtos = especialista.get("produtos")
    texto = " ".join(str(especialista.get(campo) or "") for campo in ("resposta", "recomendacao", "acompanhamento"))
    if not isinstance(produtos, list) or not produtos:
        if notas_citadas(texto):
            return ["o texto cita nota(s) mas o JSON não tem `produtos` amarrando cada nota ao seu product_id"], None
        return [], None
    nomes = nomes_por_id(evidencias)
    buscados = ids_buscados(evidencias)
    problemas = [problema for item in produtos for problema in _problemas_do_item(item, evidencias, nomes, buscados)]
    problemas += _problemas_do_texto(texto, produtos)
    if problemas:
        return problemas, None
    return [], "; ".join(_resumo_do_item(item, nomes) for item in produtos)


def _resumo_do_item(item: dict, nomes: dict[str, str]) -> str:
    product_id = str(item["product_id"]).strip()
    notas = item.get("notas") if isinstance(item.get("notas"), dict) else {}
    lista = ", ".join(f"{campo}={valor}" for campo, valor in notas.items())
    return f"product_id {product_id} = {nomes.get(product_id)}" + (f" (notas: {lista})" if lista else "")
