"""Conexão com o Qdrant e modelo de embeddings do FAQ (extra `rag`).

Tudo é criado sob demanda (nada no import): o SDK continua importável sem as
dependências do extra `rag` e sem `QDRANT_URL`, e cada processo da API abre
um único cliente e carrega o modelo uma única vez."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from venus_sdk.config.settings import QDRANT_API_KEY, QDRANT_URL

COLLECTION = "faq_chunks"

# Modelo multilíngue do FastEmbed (~50 línguas, inclui português; roda local,
# sem chave de API): 384 dimensões, 0,22 GB — leve o bastante para cada
# instância da API carregar. Ingestão e busca PRECISAM usar o mesmo modelo
# (trocar o modelo exige rodar a ingestão de novo).
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@lru_cache(maxsize=1)
def get_qdrant_client() -> Any:
    """Cliente do Qdrant apontando para `QDRANT_URL` (+ `QDRANT_API_KEY`)."""
    if not QDRANT_URL:
        raise ValueError("QDRANT_URL não configurada — defina no .env para usar o FAQ no Qdrant.")
    from qdrant_client import QdrantClient

    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


@lru_cache(maxsize=1)
def get_embed_model() -> Any:
    """Modelo de embeddings do FastEmbed (baixado no primeiro uso; o cache
    fica em `FASTEMBED_CACHE_PATH`, se definido)."""
    from llama_index.embeddings.fastembed import FastEmbedEmbedding

    return FastEmbedEmbedding(model_name=EMBEDDING_MODEL_NAME)


def get_vector_store(cliente: Any | None = None) -> Any:
    """Vector store do llama_index sobre a coleção do FAQ."""
    from llama_index.vector_stores.qdrant import QdrantVectorStore

    return QdrantVectorStore(client=cliente or get_qdrant_client(), collection_name=COLLECTION)
