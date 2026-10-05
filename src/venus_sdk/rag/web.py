"""Busca na internet — segunda fonte externa do RAG.

`buscar_web`: Tavily se `TAVILY_API_KEY` estiver definida (HTTP direto via
httpx); senão DuckDuckGo (`duckduckgo-search`/`ddgs`). Nunca levanta: erro
de rede vira `{"erro": ...}` para o agente dizer que não conseguiu consultar.

`BuscaWebMcp`: a mesma busca pelo servidor MCP oficial da Tavily (tool
`tavily_search`), que é o caminho de produção na API; se a tool MCP não
estiver carregada, falhar ou devolver algo ilegível, cai em `buscar_web`."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Callable
from typing import Any

import httpx

from venus_sdk.guardrail_rules import contem_tentativa_de_injecao

logger = logging.getLogger(__name__)

_URL_TAVILY = "https://api.tavily.com/search"
_TIMEOUT_TAVILY_SEGUNDOS = 15


def _tavily(consulta: str, chave: str, max_resultados: int) -> list[dict[str, Any]]:
    resposta = httpx.post(
        _URL_TAVILY,
        json={"api_key": chave, "query": consulta, "max_results": max_resultados},
        timeout=_TIMEOUT_TAVILY_SEGUNDOS,
    )
    resposta.raise_for_status()
    return [{"titulo": item.get("title"), "trecho": item.get("content"), "url": item.get("url")}
            for item in resposta.json().get("results", [])]


def _duckduckgo(consulta: str, max_resultados: int) -> list[dict[str, Any]]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    with DDGS() as ddgs:
        return [{"titulo": item.get("title"), "trecho": item.get("body"), "url": item.get("href")}
                for item in ddgs.text(consulta, max_results=max_resultados)]


def buscar_web(consulta: str, max_resultados: int = 3) -> list[dict[str, Any]] | dict[str, Any]:
    """`[{titulo, trecho, url}]`, `{"encontrado": False, ...}` ou `{"erro": ...}`."""
    if not (consulta or "").strip():
        return {"erro": "informe o que buscar na web"}
    try:
        chave = os.getenv("TAVILY_API_KEY")
        resultados = _tavily(consulta, chave, max_resultados) if chave else _duckduckgo(consulta, max_resultados)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Busca web falhou: %s", exc)
        return {"erro": "não consegui consultar a internet agora", "detalhe": type(exc).__name__}
    resultados = _sem_injecao(resultados)
    if not resultados:
        return {"encontrado": False, "mensagem": "nenhum resultado na web para essa consulta"}
    return resultados


def _sem_injecao(resultados: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Página da web é conteúdo de terceiros: trecho com instrução ao sistema
    # (injeção indireta) é descartado antes de chegar ao agente.
    return [
        item for item in resultados
        if not contem_tentativa_de_injecao(f"{item.get('titulo') or ''} {item.get('trecho') or ''}")
    ]


# --- busca pelo servidor MCP da Tavily ------------------------------------------
_TOOL_MCP_TAVILY = "tavily_search"
# Campos de cada resultado no texto do tavily-mcp (`formatResults`): só
# Title/URL/Content interessam; os outros encerram o Content.
_CAMPO_TAVILY = re.compile(r"^(Title|URL|Content|ID|Raw Content|Favicon|Answer|Detailed Results):\s?(.*)$")


def _texto_da_saida_mcp(saida: Any) -> str:
    """A tool MCP devolve texto ou uma lista de blocos `{"type": "text", ...}`."""
    if isinstance(saida, str):
        return saida
    if isinstance(saida, (list, tuple)):
        return "\n".join(bloco.get("text", "") if isinstance(bloco, dict) else str(bloco) for bloco in saida)
    return str(saida or "")


def resultados_do_tavily_mcp(saida: Any) -> list[dict[str, Any]]:
    """`[{titulo, trecho, url}]` a partir da saída em texto do `tavily_search`
    — o mesmo formato de `buscar_web`, para as fontes chegarem iguais ao
    Juiz e à resposta."""
    resultados: list[dict[str, Any]] = []
    atual: dict[str, Any] | None = None
    campo_aberto: str | None = None
    for linha in _texto_da_saida_mcp(saida).splitlines():
        match = _CAMPO_TAVILY.match(linha)
        if match:
            campo, valor = match.groups()
            campo_aberto = None
            if campo == "Title":
                atual = {"titulo": valor.strip(), "trecho": "", "url": None}
                resultados.append(atual)
            elif atual is not None and campo == "URL":
                atual["url"] = valor.strip()
            elif atual is not None and campo == "Content":
                atual["trecho"] = valor
                campo_aberto = "trecho"
        elif atual is not None and campo_aberto == "trecho" and linha.strip():
            atual["trecho"] = f"{atual['trecho']}\n{linha}"
    return [
        {"titulo": item["titulo"], "url": item["url"], "trecho": item["trecho"].strip()}
        for item in resultados if item["url"]
    ]


class BuscaWebMcp:
    """Busca na web pela tool MCP `tavily_search`, com `buscar_web` de reserva.

    `obter_tool` devolve a tool MCP já carregada, ou `None` enquanto a sessão
    ainda não abriu (a API abre em segundo plano, sem segurar o startup) —
    por isso é chamada a cada busca, e não guardada na montagem do grafo."""

    def __init__(self, obter_tool: Callable[[], Any | None]) -> None:
        self._obter_tool = obter_tool

    async def buscar(self, consulta: str, max_resultados: int = 3) -> list[dict[str, Any]] | dict[str, Any]:
        tool = self._obter_tool()
        if tool is not None and (consulta or "").strip():
            try:
                saida = await tool.ainvoke({"query": consulta, "max_results": max_resultados})
                resultados = resultados_do_tavily_mcp(saida)
            except Exception as erro:  # noqa: BLE001 — servidor MCP fora do ar: busca direta
                logger.warning("Busca web via MCP falhou (%s); usando a busca direta.", type(erro).__name__)
            else:
                if resultados:
                    resultados = _sem_injecao(resultados)
                    return resultados or {"encontrado": False, "mensagem": "nenhum resultado na web para essa consulta"}
                logger.warning("Busca web via MCP sem resultados legíveis; usando a busca direta.")
        return await asyncio.to_thread(buscar_web, consulta, max_resultados)
