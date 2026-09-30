"""Tools do agente FAQ — o RAG do Venus (fontes externas: documentos locais
indexados + busca na internet). Cada resultado carrega `fonte`/`url`, que o
agente deve repassar em `fontes_usadas`."""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.tools import BaseTool, tool

from venus_sdk.rag.web import buscar_web

_TRECHOS_POR_BUSCA = 3


def montar_tools_faq(indice: Any) -> list[BaseTool]:
    """Monta `faq_retriever` (índice local) e `buscar_na_web`. `indice` é um
    `IndiceRAG` (ou qualquer objeto com `.buscar(consulta, k)`); `None` levanta
    `ValueError` só no primeiro uso do nó, como nas outras tools."""
    if indice is None:
        raise ValueError(
            "montar_tools_faq requer um índice RAG — use "
            "venus_sdk.rag.criar_indice_local('data/faq') e passe via "
            "compilar_grafo_venus(indice_rag=...)."
        )

    @tool
    def faq_retriever(pergunta: str) -> list[dict] | dict:
        """Busca no FAQ oficial do Venus (documentos locais indexados) os
        trechos mais relevantes para a pergunta. Cada resultado traz o
        `trecho`, a `fonte` (arquivo) e o `score` de similaridade. Use SEMPRE
        antes de responder dúvidas sobre o Venus."""
        try:
            achados = indice.buscar(pergunta, k=_TRECHOS_POR_BUSCA)
        except Exception as exc:  # noqa: BLE001
            return {"erro": "falha ao consultar o índice do FAQ", "detalhe": type(exc).__name__}
        if not achados:
            return {"encontrado": False, "mensagem": "nenhum trecho relevante no FAQ"}
        return achados

    @tool
    async def buscar_na_web(consulta: str) -> list[dict] | dict:
        """Busca na internet (Tavily/DuckDuckGo) quando o FAQ local não
        cobrir a dúvida — ex.: informação pública sobre um ingrediente.
        Devolve `titulo`, `trecho` e `url` (a fonte que deve ser citada)."""
        return await asyncio.to_thread(buscar_web, consulta)

    return [faq_retriever, buscar_na_web]
