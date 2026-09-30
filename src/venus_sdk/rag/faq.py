"""Índice do FAQ no Qdrant e a escolha do índice que o agente FAQ usa.

`IndiceQdrant` tem a mesma interface do `IndiceRAG` local (`buscar(consulta,
k)` -> `[{trecho, fonte, score}]`), então a tool `faq_retriever`, o prompt e
o Juiz funcionam igual com qualquer um dos dois."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from venus_sdk.config.settings import FAQ_DIR, QDRANT_URL
from venus_sdk.rag.indice import IndiceRAG, criar_indice_local
from venus_sdk.rag.vector_build import get_embed_model, get_qdrant_client, get_vector_store

logger = logging.getLogger(__name__)

# Similaridade mínima (cosseno) para um trecho contar como relevante.
# Calibrado com o FAQ real e o modelo de `vector_build`: perguntas do FAQ
# ficam entre 0,34 e 0,66; perguntas sem relação, abaixo de 0,3.
_SCORE_MINIMO = 0.3
_CASAS_DECIMAIS_SCORE = 3


class IndiceQdrant:
    """Busca no FAQ gravado no Qdrant por `rag.faq_ingest.ingerir_faq`."""

    def __init__(self, cliente: Any | None = None, embed_model: Any | None = None,
                 score_minimo: float = _SCORE_MINIMO) -> None:
        self._cliente = cliente
        self._embed_model = embed_model
        self._score_minimo = score_minimo
        self._indice: Any | None = None

    def _indice_llama(self) -> Any:
        if self._indice is None:
            from llama_index.core import VectorStoreIndex

            self._indice = VectorStoreIndex.from_vector_store(
                get_vector_store(self._cliente or get_qdrant_client()),
                embed_model=self._embed_model or get_embed_model(),
            )
        return self._indice

    def buscar(self, consulta: str, k: int = 3, score_minimo: float | None = None) -> list[dict[str, Any]]:
        """Top-k trechos acima de `score_minimo` (padrão: o do construtor).
        Vazio = nada relevante; o agente deve dizer que não sabe, nunca inventar."""
        if not consulta.strip() or k <= 0:
            return []
        minimo = self._score_minimo if score_minimo is None else score_minimo
        nos = self._indice_llama().as_retriever(similarity_top_k=k).retrieve(consulta)
        return [
            {
                "trecho": no.get_content(),
                "fonte": no.metadata.get("file_name"),
                "score": round(no.score, _CASAS_DECIMAIS_SCORE),
            }
            for no in nos
            if no.score is not None and no.score >= minimo
        ]


def criar_indice_faq(pasta: str | Path = FAQ_DIR, *, cache: str | Path | None = None) -> IndiceQdrant | IndiceRAG:
    """Índice do FAQ para `compilar_grafo_venus(indice_rag=...)`: o Qdrant
    quando `QDRANT_URL` está configurada (produção — o conteúdo vem de
    `faq_ingest`); senão o índice local em memória sobre `pasta` (dev/testes),
    com `cache` opcional (ver `criar_indice_local`)."""
    if QDRANT_URL:
        logger.info("FAQ: usando a coleção do Qdrant.")
        return IndiceQdrant()
    logger.info("FAQ: QDRANT_URL ausente — usando o índice local de %s.", pasta)
    return criar_indice_local(pasta, cache=cache)
