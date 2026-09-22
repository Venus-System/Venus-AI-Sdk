from langchain.tools import tool
from rag.vector_build import qdrant, gerar_embedding, COLLECTION

@tool
def faq_retriever(pergunta: str):
    """Busca trechos importantes revelantes para responder a pergunta do usuário"""
    vetor = gerar_embedding(pergunta)
    resultados = qdrant.query_points(
        collection_name=COLLECTION,
        query=vetor,
        limit= 6
    )

    if not resultados.points:
        return "nenhum trecho combina com o texto dado"
    
    return "\n\n".join(
        ponto.payload for ponto in resultados.points)