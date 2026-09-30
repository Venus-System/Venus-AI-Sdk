"""RAG: carregador, índice vetorial, tools do FAQ e busca na web (HTTP mockado)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from venus_sdk.rag import EmbeddingsHash, IndiceRAG, buscar_web, carregar_documentos, criar_indice_local
from venus_sdk.tools.faq import montar_tools_faq

FAQ = Path(__file__).resolve().parents[2] / "data" / "faq"


def test_carregador_gera_chunks_com_fonte() -> None:
    docs = carregar_documentos(FAQ)
    assert docs
    assert {d.metadata["fonte"] for d in docs} >= {"privacidade_e_dados.md", "como_funciona_o_score.md"}
    assert all("trecho" in d.metadata for d in docs)


def test_carregador_pasta_inexistente() -> None:
    with pytest.raises(FileNotFoundError):
        carregar_documentos("/nao/existe")


@pytest.mark.parametrize("pergunta,fonte", [
    ("Como o Venus calcula o score dos produtos?", "como_funciona_o_score.md"),
    ("meus dados pessoais são compartilhados com anunciantes?", "privacidade_e_dados.md"),
    ("como cadastro minhas alergias?", "alergias_e_limites.md"),
])
def test_indice_recupera_a_fonte_certa(pergunta: str, fonte: str) -> None:
    resultados = criar_indice_local(FAQ).buscar(pergunta, k=1)
    assert resultados and resultados[0]["fonte"] == fonte


def test_indice_sem_relevancia_devolve_vazio() -> None:
    assert criar_indice_local(FAQ).buscar("zzz qqq xyzw", k=3) == []


def test_indice_cache_em_disco(tmp_path: Path) -> None:
    cache = tmp_path / "idx.npz"
    a = criar_indice_local(FAQ, cache=cache)
    assert cache.exists()
    b = criar_indice_local(FAQ, cache=cache)
    assert (a.matriz == b.matriz).all()


def test_embeddings_injetaveis() -> None:
    from langchain_core.documents import Document

    idx = IndiceRAG([Document(page_content="alfa beta", metadata={"fonte": "x"})], EmbeddingsHash(64))
    assert idx.buscar("alfa")[0]["fonte"] == "x"


def test_tools_faq_sem_indice_levanta() -> None:
    with pytest.raises(ValueError, match="índice"):
        montar_tools_faq(None)


def test_faq_retriever_devolve_trechos_e_nao_encontrado() -> None:
    tools = {t.name: t for t in montar_tools_faq(criar_indice_local(FAQ))}
    achados = tools["faq_retriever"].invoke({"pergunta": "como funciona o score?"})
    assert achados[0]["fonte"] == "como_funciona_o_score.md" and achados[0]["trecho"]
    vazio = tools["faq_retriever"].invoke({"pergunta": "zzz qqq xyzw"})
    assert vazio["encontrado"] is False


def test_faq_retriever_erro_do_indice_vira_resposta_estruturada() -> None:
    class Quebrado:
        def buscar(self, *a, **k):
            raise RuntimeError("boom")

    r = {t.name: t for t in montar_tools_faq(Quebrado())}["faq_retriever"].invoke({"pergunta": "x"})
    assert "erro" in r


def test_buscar_na_web_devolve_titulo_trecho_url() -> None:
    falso = [{"titulo": "T", "trecho": "corpo", "url": "https://ex.com"}]
    with patch("venus_sdk.tools.faq.buscar_web", return_value=falso):
        tools = {t.name: t for t in montar_tools_faq(criar_indice_local(FAQ))}
        r = asyncio.run(tools["buscar_na_web"].ainvoke({"consulta": "niacinamida"}))
    assert r == falso


def test_buscar_web_duckduckgo_mockado(monkeypatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with patch("venus_sdk.rag.web._duckduckgo", return_value=[{"titulo": "a", "trecho": "b", "url": "u"}]):
        assert buscar_web("x")[0]["url"] == "u"


def test_buscar_web_tavily_quando_ha_chave(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    with patch("venus_sdk.rag.web._tavily", return_value=[{"titulo": "t", "trecho": "c", "url": "u"}]) as t:
        assert buscar_web("x")[0]["titulo"] == "t"
    assert t.call_args.args[1] == "k"


def test_buscar_web_falha_de_rede_nao_levanta(monkeypatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with patch("venus_sdk.rag.web._duckduckgo", side_effect=OSError("sem rede")):
        r = buscar_web("x")
    assert "erro" in r
    assert "erro" in buscar_web("  ")
