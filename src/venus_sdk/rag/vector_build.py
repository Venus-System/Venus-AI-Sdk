from venus_sdk.llm.models import get_llm_embedding
from venus_sdk.config.settings import QDRANT_API, QDRANT_URL
from qdrant_client import QdrantClient


qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API)

def gerar_embedding(pergunta: str) -> list[float]:
    """Gera um vetor de 768 dimensões à partir da
    pergunta ou texto informado"""

    if not pergunta:
        raise ValueError(
            "gerar_embedding requer uma mensagem do usuário, "
            "não é possível gerar um vetor com texto vazio"
        )
    
    return get_llm_embedding().embed_query(pergunta)

def gerar_embedding_batch(textos : list[str]) -> list[list[float]]:
    """Gera vetores de múltiplos textos de uma vez"""
    if not textos:
        raise ValueError(
            "gerar_embedding requer uma mensagem do usuário, "
            "não é possível gerar um vetor com texto vazio"
        )
    return get_llm_embedding().embed_documents(textos)