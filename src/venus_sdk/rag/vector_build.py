from llama_index.embeddings.fastembed import FastEmbedEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from venus_sdk.config.settings import QDRANT_API, QDRANT_URL

COLLECTION = "faq_chunks"

# Modelo multilíngue FastEmbed que faz embeddings de 768 dimensões
EMBEDDING_MODEL_NAME = "intfloat/multilingual-e5-base"

qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API)

_embed_model: FastEmbedEmbedding | None = None
_vector_store: QdrantVectorStore | None = None


def get_embed_model() -> FastEmbedEmbedding:
    """
    Retorna o modelo de Embedding
    """
    global _embed_model
    if _embed_model is None:
        _embed_model = FastEmbedEmbedding(model_name=EMBEDDING_MODEL_NAME)
    return _embed_model


def get_vector_store() -> QdrantVectorStore:
    """
    Retorna o Vector Store
    """
    global _vector_store
    if _vector_store is None:
        _vector_store = QdrantVectorStore(client=qdrant, collection_name=COLLECTION)
    return _vector_store