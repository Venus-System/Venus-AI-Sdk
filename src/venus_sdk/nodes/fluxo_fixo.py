"""Consulta das tools de Produto e Ingrediente sem LLM, para o roteiro que é sempre
o mesmo: achar o item pelo texto da pergunta e trazer tudo sobre ele.

Devolve `None` quando o roteiro não resolve (item não achado, ambiguidade, "dele"
sem produto em tela, cálculo), e aí o nó cai no agente ReAct."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from venus_sdk.llm.models import extrair_texto_resposta, get_llm_rapido
from venus_sdk.prompts.faq import ESCOLHA_DE_TRECHO_PROMPT
from venus_sdk.tools._util import palavras_de_busca

logger = logging.getLogger(__name__)

_MAX_PRODUTOS_SUGERIDOS = 5
_MAX_PALAVRAS_DO_INGREDIENTE = 3
_RE_SUGESTAO = re.compile(
    r"\b(indic\w*|sugir\w*|sugest\w*|recomend\w*|melhor(es)?|(bom|boa|bons|boas) (pra|para))\b", re.IGNORECASE
)
_RE_REFERENCIA_AO_CONTEXTO = re.compile(
    r"\b(dele|dela|deles|delas|esse|essa|este|esta|desse|dessa|deste|desta|nesse|nessa|primeiro|segundo|terceiro|"
    r"[uú]ltimo|mesmo|outro|outra)\b",
    re.IGNORECASE,
)
# Conta com número dado pelo usuário: quem faz é a tool de cálculo, via agente.
_RE_CALCULO = re.compile(r"\d")
# Sozinhas, casam com centenas de ingredientes sem dizer qual é.
_PALAVRAS_GENERICAS = {"acido", "oleo", "extrato", "pele", "cabelo", "vitamina", "mal", "serve", "faz", "pode", "usar"}


async def _chamar(tools: dict[str, Any], nome: str, **argumentos: Any) -> dict[str, Any]:
    resultado = await tools[nome].ainvoke(argumentos)
    return {"tool": nome, "argumentos": argumentos,
            "resultado": resultado if isinstance(resultado, str) else json.dumps(resultado, ensure_ascii=False)}


def _dados(evidencia: dict[str, Any]) -> Any:
    return json.loads(evidencia["resultado"])


async def _do_usuario(tools: dict[str, Any], usuario: int | None) -> list[dict[str, Any]]:
    if usuario is None:
        return []
    return list(await asyncio.gather(
        _chamar(tools, "get_user_allergies", user_id=usuario), _chamar(tools, "get_user_profile", user_id=usuario)
    ))


def _produto_citado(candidatos: list[dict], palavras: set[str]) -> dict | None:
    """O candidato cujo nome inteiro está na pergunta (o mais longo) ou, sem isso, o
    único com a maior relevância. Empate é ambiguidade: quem resolve é o agente."""
    citados = [c for c in candidatos if palavras_de_busca(c["name"]) and set(palavras_de_busca(c["name"])) <= palavras]
    if citados:
        return max(citados, key=lambda c: len(palavras_de_busca(c["name"])))
    maior = max(c.get("relevancia", 0) for c in candidatos)
    primeiros = [c for c in candidatos if c.get("relevancia", 0) == maior]
    return primeiros[0] if len(primeiros) == 1 else None


async def evidencias_de_produto(
    pergunta: str, produto_em_contexto: str | None, usuario: int | None, tools: dict[str, Any]
) -> tuple[list[dict[str, Any]], str] | None:
    """`(evidências, intenção)` da pergunta de produto, ou `None` para usar o agente."""
    if not produto_em_contexto and _RE_REFERENCIA_AO_CONTEXTO.search(pergunta):
        return None
    if _RE_CALCULO.search(pergunta):
        return None
    sugestao = bool(_RE_SUGESTAO.search(pergunta))
    termo = produto_em_contexto if produto_em_contexto and not sugestao else pergunta
    busca = await _chamar(tools, "search_product", termo=termo)
    candidatos = _dados(busca)
    if not isinstance(candidatos, list) or not candidatos:
        return None
    if sugestao:
        escolhidos, intencao = candidatos[:_MAX_PRODUTOS_SUGERIDOS], "sugerir"
    else:
        produto = _produto_citado(candidatos, set(palavras_de_busca(termo)))
        if produto is None:
            return None
        escolhidos, intencao = [produto], "informar"
    consultas = []
    for candidato in escolhidos:
        product_id = candidato["product_id"]
        consultas += [_chamar(tools, "get_product_score", product_id=product_id),
                      _chamar(tools, "get_product_ingredients", product_id=product_id)]
        if intencao == "informar":
            consultas.append(_chamar(tools, "get_product", product_id=product_id))
            if usuario is not None:
                consultas.append(_chamar(tools, "get_personalized_score", product_id=product_id, user_id=usuario))
    detalhes = await asyncio.gather(*consultas)
    return [busca, *detalhes, *await _do_usuario(tools, usuario)], intencao


async def evidencias_de_ingrediente(
    pergunta: str, usuario: int | None, tools: dict[str, Any]
) -> tuple[list[dict[str, Any]], str] | None:
    """`(evidências, intenção)` da pergunta de ingrediente, ou `None` para usar o agente."""
    if _RE_CALCULO.search(pergunta) or _RE_REFERENCIA_AO_CONTEXTO.search(pergunta):
        return None
    palavras = palavras_de_busca(pergunta)
    trechos = [
        " ".join(palavras[i:i + tamanho])
        for tamanho in range(_MAX_PALAVRAS_DO_INGREDIENTE, 0, -1)
        for i in range(len(palavras) - tamanho + 1)
        if tamanho > 1 or palavras[i] not in _PALAVRAS_GENERICAS
    ]
    for trecho in trechos:
        busca = await _chamar(tools, "search_ingredient", termo=trecho)
        candidatos = _dados(busca)
        if isinstance(candidatos, list) and candidatos:
            break
    else:
        return None
    if len(candidatos) > 1:
        return [busca, *await _do_usuario(tools, usuario)], "informar"
    ingredient_id = candidatos[0]["ingredient_id"]
    detalhes = await asyncio.gather(*(
        _chamar(tools, nome, ingredient_id=ingredient_id)
        for nome in ("get_ingredient_summary", "get_ingredient_properties", "get_ingredient_effects",
                     "get_ingredient_regulations")
    ))
    return [busca, *detalhes, *await _do_usuario(tools, usuario)], "informar"


def _escolher_trecho(pergunta: str, trechos: list[str]) -> int | None:
    """Índice do trecho que responde à pergunta, segundo o LLM rápido, ou `None`."""
    numerados = "\n\n".join(f"[{numero}] {trecho}" for numero, trecho in enumerate(trechos, start=1))
    mensagens = [("system", ESCOLHA_DE_TRECHO_PROMPT), ("human", f"PERGUNTA={pergunta}\n\nTRECHOS:\n{numerados}")]
    try:
        saida = extrair_texto_resposta(get_llm_rapido().invoke(mensagens))
    except Exception:  # noqa: BLE001 — sem a escolha, a resposta é dizer que não encontrou
        logger.exception("Falha ao escolher o trecho do FAQ")
        return None
    achado = re.search(r"\d+", saida)
    indice = int(achado.group()) - 1 if achado else -1
    return indice if 0 <= indice < len(trechos) else None


async def evidencias_de_faq(
    pergunta: str, tools: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """`(evidências, fontes a mostrar)`: o trecho do FAQ que responde, ou nada. Sem web:
    ela não sabe sobre o app ("quem criou o aplicativo?" trazia a página do WhatsApp)."""
    busca = await _chamar(tools, "faq_retriever", pergunta=pergunta)
    achados = _dados(busca)
    if isinstance(achados, list) and achados:
        indice = await asyncio.to_thread(_escolher_trecho, pergunta, [str(a.get("trecho")) for a in achados])
        if indice is not None:
            achado = achados[indice]
            corpo = str(achado.get("trecho")).split("\n\n", 1)[-1].strip()
            return [busca], [{"texto": corpo, "fonte": str(achado.get("fonte"))}]
    return [busca], []
