"""Índice do FAQ no Qdrant e a escolha do índice que o agente FAQ usa.

`IndiceQdrant` tem a mesma interface do `IndiceRAG` local (`buscar(consulta,
k)` -> `[{trecho, fonte, score}]`), então a tool `faq_retriever`, o prompt e
o Juiz funcionam igual com qualquer um dos dois."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from langchain_core.embeddings import Embeddings

from venus_sdk.config.settings import FAQ_DIR, QDRANT_URL
from venus_sdk.rag.indice import IndiceRAG, criar_indice_local
from venus_sdk.rag.vector_build import get_embed_model, get_qdrant_client, get_vector_store

logger = logging.getLogger(__name__)

# Similaridade mínima (cosseno) para um trecho contar como relevante, no
# Qdrant e no índice local semântico (mesmo modelo). Escolhido com
# `tests/manual/avaliar_rag.py` (28 perguntas, 6 sem resposta no FAQ):
#   Qdrant  0,30 -> hit@3 86%, MRR 0,80, "não sei" certo 83%
#           0,35 -> hit@3 77% (perde acertos sem ganhar nos "não sei")
#           0,40 -> "não sei" 100%, mas hit@3 cai para 64%
#   local   0,25 a 0,35 -> hit@3 77%, MRR 0,62, "não sei" 83%
# Reavaliar quando os documentos de venus_sdk/data/faq/ mudarem.
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


class EmbeddingsFastEmbed(Embeddings):
    """O modelo FastEmbed de `vector_build` (o mesmo da coleção do Qdrant) na
    interface de embeddings do LangChain, para o índice local."""

    def __init__(self, modelo: Any) -> None:
        self._modelo = modelo

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._modelo.get_text_embedding_batch(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._modelo.get_query_embedding(text)


def _embeddings_semanticos() -> Embeddings | None:
    """FastEmbed se o extra `rag` estiver instalado e o modelo carregar
    (o primeiro uso baixa ~220 MB); `None` senão. `VENUS_EMBEDDINGS_LOCAIS=hash`
    força o fallback (testes e uso offline)."""
    if os.getenv("VENUS_EMBEDDINGS_LOCAIS", "").lower() == "hash":
        return None
    try:
        return EmbeddingsFastEmbed(get_embed_model())
    except Exception as exc:  # noqa: BLE001 — extra ausente, sem rede para baixar o modelo
        logger.warning("FastEmbed indisponível para o índice local do FAQ (%s)", type(exc).__name__)
        return None


def criar_indice_faq(pasta: str | Path = FAQ_DIR, *, cache: str | Path | None = None) -> IndiceQdrant | IndiceRAG:
    """Índice do FAQ para `compilar_grafo_venus(indice_rag=...)`: o Qdrant
    quando `QDRANT_URL` está configurada (produção — o conteúdo vem de
    `faq_ingest`); senão o índice local em memória sobre `pasta` (dev/testes),
    com `cache` opcional (ver `criar_indice_local`)."""
    if QDRANT_URL:
        logger.info("FAQ: usando a coleção do Qdrant.")
        return IndiceQdrant()
    logger.info("FAQ: QDRANT_URL ausente — usando o índice local de %s.", pasta)
    embeddings = _embeddings_semanticos()
    if embeddings is not None:
        # Mesmo modelo (e mesma escala de nota) da coleção do Qdrant.
        return criar_indice_local(pasta, embeddings, score_minimo=_SCORE_MINIMO)
    logger.warning(
        "FAQ: índice local com EmbeddingsHash (contagem de palavras, não é busca semântica) — "
        "instale o extra `rag` do SDK para usar o FastEmbed. Aceitável só em testes/offline."
    )
    return criar_indice_local(pasta, cache=cache)
