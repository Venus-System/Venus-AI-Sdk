"""Busca na internet — segunda fonte externa do RAG.

Usa Tavily se `TAVILY_API_KEY` estiver definida (HTTP direto via httpx);
senão DuckDuckGo (`duckduckgo-search`/`ddgs`). Nunca levanta: erro de rede
vira `{"erro": ...}` para o agente dizer que não conseguiu consultar."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _tavily(consulta: str, chave: str, max_resultados: int) -> list[dict[str, Any]]:
    import httpx

    r = httpx.post(
        "https://api.tavily.com/search",
        json={"api_key": chave, "query": consulta, "max_results": max_resultados},
        timeout=15,
    )
    r.raise_for_status()
    return [{"titulo": x.get("title"), "trecho": x.get("content"), "url": x.get("url")}
            for x in r.json().get("results", [])]


def _duckduckgo(consulta: str, max_resultados: int) -> list[dict[str, Any]]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    with DDGS() as ddgs:
        return [{"titulo": x.get("title"), "trecho": x.get("body"), "url": x.get("href")}
                for x in ddgs.text(consulta, max_results=max_resultados)]


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
