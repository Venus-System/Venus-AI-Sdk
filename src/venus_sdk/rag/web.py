"""Busca na internet — segunda fonte externa do RAG.

Usa Tavily se `TAVILY_API_KEY` estiver definida (HTTP direto via httpx);
senão DuckDuckGo (`duckduckgo-search`/`ddgs`). Nunca levanta: erro de rede
vira `{"erro": ...}` para o agente dizer que não conseguiu consultar."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_URL_TAVILY = "https://api.tavily.com/search"
_TIMEOUT_TAVILY_SEGUNDOS = 15


def _tavily(consulta: str, chave: str, max_resultados: int) -> list[dict[str, Any]]:
    import httpx

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
    if not resultados:
        return {"encontrado": False, "mensagem": "nenhum resultado na web para essa consulta"}
    return resultados
