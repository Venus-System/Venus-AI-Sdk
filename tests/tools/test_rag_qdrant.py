"""FAQ no Qdrant: ingestão + busca num Qdrant em memória, com embeddings
determinísticos (sem baixar o modelo do FastEmbed)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")
pytest.importorskip("llama_index.vector_stores.qdrant")

from llama_index.core.embeddings import BaseEmbedding  # noqa: E402
from qdrant_client import QdrantClient  # noqa: E402

from venus_sdk.config.settings import FAQ_DIR  # noqa: E402
from venus_sdk.rag import EmbeddingsHash, IndiceQdrant, IndiceRAG, criar_indice_faq  # noqa: E402
from venus_sdk.rag import faq as modulo_faq  # noqa: E402
from venus_sdk.rag import vector_build  # noqa: E402
from venus_sdk.rag.faq_ingest import ingerir_faq  # noqa: E402
from venus_sdk.rag.vector_build import COLLECTION  # noqa: E402
from venus_sdk.tools.faq import montar_tools_faq  # noqa: E402

FAQ = Path(FAQ_DIR)
_SEM_CORTE = 0.0


class _EmbedHash(BaseEmbedding):
    """`EmbeddingsHash` do índice local no formato do llama_index."""

    def _vetor(self, texto: str) -> list[float]:
        return EmbeddingsHash(256).embed_query(texto)

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._vetor(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._vetor(text)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._vetor(query)


@pytest.fixture
def cliente() -> QdrantClient:
    return QdrantClient(":memory:")


@pytest.fixture
def indice(cliente: QdrantClient) -> IndiceQdrant:
    ingerir_faq(FAQ, cliente=cliente, embed_model=_EmbedHash())
    return IndiceQdrant(cliente=cliente, embed_model=_EmbedHash())


def test_ingestao_grava_trechos_e_reingestao_nao_duplica(cliente: QdrantClient) -> None:
    total = ingerir_faq(FAQ, cliente=cliente, embed_model=_EmbedHash())
    assert total > 0
    assert cliente.count(COLLECTION).count == total

    assert ingerir_faq(FAQ, cliente=cliente, embed_model=_EmbedHash()) == total
    assert cliente.count(COLLECTION).count == total


def test_ingestao_pasta_inexistente(cliente: QdrantClient) -> None:
    with pytest.raises(FileNotFoundError):
        ingerir_faq("/nao/existe", cliente=cliente, embed_model=_EmbedHash())


def test_busca_devolve_trecho_fonte_e_score(indice: IndiceQdrant) -> None:
    achados = indice.buscar("Quais dados o Venus coleta?", k=3, score_minimo=_SEM_CORTE)
    assert achados and len(achados) <= 3
    assert all(set(a) == {"trecho", "fonte", "score"} for a in achados)
    assert all(a["fonte"].endswith(".md") and a["trecho"] for a in achados)


def test_busca_com_corte_alto_devolve_vazio(indice: IndiceQdrant) -> None:
    assert indice.buscar("zzz qqq xyzw", k=3, score_minimo=0.99) == []
    assert indice.buscar("   ") == []


def test_faq_retriever_funciona_com_o_indice_qdrant(cliente: QdrantClient) -> None:
    ingerir_faq(FAQ, cliente=cliente, embed_model=_EmbedHash())
    indice = IndiceQdrant(cliente=cliente, embed_model=_EmbedHash(), score_minimo=_SEM_CORTE)
    faq_retriever = {t.name: t for t in montar_tools_faq(indice)}["faq_retriever"]
    achados = faq_retriever.invoke({"pergunta": "Quais dados o Venus coleta?"})
    assert isinstance(achados, list) and achados[0]["fonte"]


def test_criar_indice_faq_escolhe_pelo_qdrant_url(monkeypatch) -> None:
    monkeypatch.setattr(modulo_faq, "QDRANT_URL", None)
    assert isinstance(criar_indice_faq(FAQ), IndiceRAG)
    monkeypatch.setattr(modulo_faq, "QDRANT_URL", "http://qdrant:6333")
    assert isinstance(criar_indice_faq(FAQ), IndiceQdrant)


def test_cliente_sem_qdrant_url_levanta(monkeypatch) -> None:
    monkeypatch.setattr(vector_build, "QDRANT_URL", None)
    vector_build.get_qdrant_client.cache_clear()
    with pytest.raises(ValueError, match="QDRANT_URL"):
        vector_build.get_qdrant_client()


class _EmbedQuebrado(_EmbedHash):
    def _get_text_embedding(self, text: str) -> list[float]:
        raise RuntimeError("provedor de embeddings fora do ar")

    def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("provedor de embeddings fora do ar")


def test_ingestao_que_falha_no_meio_mantem_o_faq_anterior(cliente: QdrantClient) -> None:
    total = ingerir_faq(FAQ, cliente=cliente, embed_model=_EmbedHash())
    with pytest.raises(RuntimeError):
        ingerir_faq(FAQ, cliente=cliente, embed_model=_EmbedQuebrado())
    assert cliente.count(COLLECTION).count == total
