"""Ingestão do FAQ no Qdrant: lê os `.md` da pasta, divide em trechos, gera os
embeddings e substitui o conteúdo da coleção.

Roda uma vez por atualização dos documentos, fora da API:

    python -m venus_sdk.rag.faq_ingest            # usa FAQ_DIR
    python -m venus_sdk.rag.faq_ingest minha/pasta   # outra pasta

A API só consulta a coleção (`rag.faq.IndiceQdrant`), então nenhuma instância
precisa dos arquivos nem reindexa nada ao subir."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from venus_sdk.config.settings import FAQ_DIR
from venus_sdk.rag.vector_build import COLLECTION, get_embed_model, get_qdrant_client, get_vector_store

logger = logging.getLogger(__name__)

# O modelo lê no máximo 512 tokens: seções longas do markdown são subdivididas.
_TAMANHO_TRECHO = 400
_SOBREPOSICAO = 50
_PONTOS_POR_PAGINA = 256


def ingerir_faq(pasta: str | Path = FAQ_DIR, *, cliente: Any | None = None, embed_model: Any | None = None) -> int:
    """Indexa os `.md` de `pasta` na coleção do FAQ e devolve quantos trechos
    foram gravados. `cliente`/`embed_model` permitem injetar outros (testes)."""
    from llama_index.core import SimpleDirectoryReader, StorageContext, VectorStoreIndex
    from llama_index.core.node_parser import MarkdownNodeParser, SentenceSplitter

    pasta = Path(pasta)
    if not pasta.is_dir():
        raise FileNotFoundError(f"Pasta de documentos do FAQ não encontrada: {pasta}")

    documentos = SimpleDirectoryReader(input_dir=str(pasta), required_exts=[".md"]).load_data()
    if not documentos:
        logger.warning("Nenhum arquivo .md em %s — nada foi indexado.", pasta)
        return 0

    trechos = SentenceSplitter(chunk_size=_TAMANHO_TRECHO, chunk_overlap=_SOBREPOSICAO)(
        MarkdownNodeParser().get_nodes_from_documents(documentos)
    )
    cliente = cliente or get_qdrant_client()
    # Os antigos só saem depois que os novos estão gravados: se a ingestão
    # falhar no meio, o FAQ continua no ar com o conteúdo anterior.
    ids_antigos = _ids_dos_pontos(cliente)
    VectorStoreIndex(
        trechos,
        storage_context=StorageContext.from_defaults(vector_store=get_vector_store(cliente)),
        embed_model=embed_model or get_embed_model(),
    )
    _apagar_pontos(cliente, ids_antigos)
    logger.info("%d documento(s), %d trecho(s) indexado(s) na coleção '%s'.", len(documentos), len(trechos), COLLECTION)
    return len(trechos)


def _ids_dos_pontos(cliente: Any) -> list[Any]:
    """Ids de todos os pontos já gravados na coleção (vazio se ela não existe)."""
    if not cliente.collection_exists(COLLECTION):
        return []
    ids: list[Any] = []
    proximo = None
    while True:
        pontos, proximo = cliente.scroll(
            COLLECTION, limit=_PONTOS_POR_PAGINA, offset=proximo, with_payload=False, with_vectors=False
        )
        ids += [ponto.id for ponto in pontos]
        if proximo is None:
            return ids


def _apagar_pontos(cliente: Any, ids: list[Any]) -> None:
    from qdrant_client import models

    if ids:
        cliente.delete(collection_name=COLLECTION, points_selector=models.PointIdsList(points=ids))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[ingest] %(message)s")
    ingerir_faq(*sys.argv[1:2])
