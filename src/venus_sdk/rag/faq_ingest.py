from llama_index.core import SimpleDirectoryReader, StorageContext, VectorStoreIndex
from llama_index.core.node_parser import MarkdownNodeParser
from venus_sdk.config.settings import FAQ_PATH
from venus_sdk.rag.vector_build import get_embed_model, get_vector_store, qdrant, COLLECTION
from qdrant_client import models

def ingerir_faq() -> int:
    """Lê os arquivos da pasta configurada, gera os embeddings e envia à collection no Qdrant."""

    if not FAQ_PATH.exists():
        raise ValueError("Pasta de documentos não encontrada!")

    documentos = SimpleDirectoryReader(
        input_dir=str(FAQ_PATH),
        required_exts=[".md"],
    ).load_data()

    if not documentos:
        print("[ingest] Nenhum arquivo .md encontrado para indexar.")
        return 0

    print(f"[ingest] Documentos carregados: {len(documentos)}")

    if qdrant.collection_exists(COLLECTION):
        info = qdrant.get_collection(COLLECTION)
        if info.points_count > 0:
            print(f"[ingest] Limpando {info.points_count} ponto(s) existente(s) da coleção '{COLLECTION}'...")
            qdrant.delete(
                collection_name=COLLECTION,
                points_selector=models.FilterSelector(filter=models.Filter(must=[])),
            )

    node_parser = MarkdownNodeParser()
    storage_context = StorageContext.from_defaults(vector_store=get_vector_store())

    index = VectorStoreIndex.from_documents(
        documentos,
        storage_context=storage_context,
        transformations=[node_parser],
        embed_model=get_embed_model(),
        show_progress=True,
    )

    total = len(index.docstore.docs)
    print(f"[ingest] Concluído! {total} chunk(s) indexado(s) no Qdrant.")
    return total


if __name__ == "__main__":
    ingerir_faq()