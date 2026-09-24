from langchain.tools import tool
from llama_index.core import VectorStoreIndex

from venus_sdk.rag.vector_build import get_embed_model, get_vector_store


@tool
def faq_retriever(pergunta: str) -> str:
    """Busca trechos importantes dentro do Qdrant para responder a pergunta do usuário."""

    if not pergunta or not pergunta.strip():
        return "Não é possível buscar com uma pergunta vazia."

    try:
        index = VectorStoreIndex.from_vector_store(
            get_vector_store(),
            embed_model=get_embed_model(),
        )
        retriever = index.as_retriever(similarity_top_k=6)
        nos = retriever.retrieve(pergunta)
    except Exception as exc:
        return "Não foi possível buscar os documentos no momento."

    if not nos:
        return "Nenhum trecho combina com o texto dado."

    blocos = []
    for no in nos:
        fonte = no.metadata.get("file_name", "desconhecido")
        blocos.append(f"[Fonte: {fonte} \n{no.get_content()}")

    return "\n\n---\n\n".join(blocos)