"""RAG do Venus: documentos do FAQ, índice vetorial (Qdrant ou local) e busca na web."""

from venus_sdk.rag.carregador import carregar_documentos
from venus_sdk.rag.faq import IndiceQdrant, criar_indice_faq
from venus_sdk.rag.indice import EmbeddingsHash, IndiceRAG, criar_indice_local
from venus_sdk.rag.web import buscar_web

__all__ = [
    "carregar_documentos",
    "criar_indice_faq",
    "IndiceQdrant",
    "EmbeddingsHash",
    "IndiceRAG",
    "criar_indice_local",
    "buscar_web",
]
