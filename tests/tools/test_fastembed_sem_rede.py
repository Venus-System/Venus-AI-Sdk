"""Item 4 da revisão técnica 3: sem o modelo do FastEmbed em cache e sem rede,
o índice local do FAQ cai no EmbeddingsHash na hora, em vez de esperar as
tentativas de download (3 + 9 + 27 s) da biblioteca."""

import logging
import threading
import time
from pathlib import Path

import pytest

from venus_sdk.config.settings import FAQ_DIR
from venus_sdk.rag import faq, vector_build
from venus_sdk.rag.indice import EmbeddingsHash


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch, tmp_path):
    """Cache vazio, sem Qdrant e com o caminho semântico liberado (a suíte usa
    VENUS_EMBEDDINGS_LOCAIS=hash por padrão)."""
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "cache-vazio"))
    monkeypatch.delenv("VENUS_EMBEDDINGS_LOCAIS", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("VENUS_FASTEMBED_DOWNLOAD", raising=False)
    monkeypatch.delenv("VENUS_FASTEMBED_TIMEOUT_SEGUNDOS", raising=False)
    monkeypatch.setattr(faq, "QDRANT_URL", None)
    vector_build.get_embed_model.cache_clear()
    yield
    vector_build.get_embed_model.cache_clear()


@pytest.fixture
def criacoes(monkeypatch):
    """Troca a criação do modelo (que baixaria ~220 MB) por um registro."""
    chamadas = []

    def criar(*, somente_local):
        chamadas.append(somente_local)
        return object()

    monkeypatch.setattr(vector_build, "_criar_fastembed", criar)
    return chamadas


def _cache_com_o_modelo(pasta: Path) -> None:
    snapshot = pasta / f"models--{vector_build._REPO_DO_MODELO_NO_HF.replace('/', '--')}" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / vector_build._ARQUIVO_DO_MODELO).write_bytes(b"onnx")


def test_offline_e_sem_cache_cai_no_hash_em_menos_de_1s(monkeypatch, caplog):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    inicio = time.monotonic()
    indice = faq.criar_indice_faq(FAQ_DIR)
    assert time.monotonic() - inicio < 1
    assert isinstance(indice.embeddings, EmbeddingsHash)
    assert any("EmbeddingsHash" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


def test_download_proibido_nao_tenta_baixar(monkeypatch, criacoes):
    monkeypatch.setenv("VENUS_FASTEMBED_DOWNLOAD", "0")
    with pytest.raises(vector_build.ModeloIndisponivel):
        vector_build.get_embed_model()
    assert criacoes == []
    assert faq._embeddings_semanticos() is None


def test_download_proibido_usa_o_cache_sem_rede(monkeypatch, tmp_path, criacoes):
    monkeypatch.setenv("VENUS_FASTEMBED_DOWNLOAD", "0")
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "cache"))
    _cache_com_o_modelo(tmp_path / "cache")
    vector_build.get_embed_model()
    assert criacoes == [True]  # só local_files_only


def test_com_o_modelo_em_cache_tambem_nao_vai_na_rede(monkeypatch, tmp_path, criacoes):
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "cache"))
    _cache_com_o_modelo(tmp_path / "cache")
    vector_build.get_embed_model()
    assert criacoes == [True]


def test_download_permitido_tem_tempo_maximo(monkeypatch):
    liberar = threading.Event()

    def criar_devagar(*, somente_local):
        liberar.wait(10)  # simula as tentativas com espera da biblioteca
        return object()

    monkeypatch.setattr(vector_build, "_criar_fastembed", criar_devagar)
    monkeypatch.setenv("VENUS_FASTEMBED_TIMEOUT_SEGUNDOS", "0.3")
    inicio = time.monotonic()
    try:
        with pytest.raises(vector_build.ModeloIndisponivel):
            vector_build.get_embed_model()
        assert time.monotonic() - inicio < 2
    finally:
        liberar.set()


def test_download_permitido_e_rapido_devolve_o_modelo(monkeypatch, criacoes):
    modelo = vector_build.get_embed_model()
    assert modelo is not None and criacoes == [False]


def test_constantes_do_modelo_batem_com_o_fastembed():
    fastembed = pytest.importorskip("fastembed")
    [descricao] = [m for m in fastembed.TextEmbedding._list_supported_models()
                   if m.model == vector_build.EMBEDDING_MODEL_NAME]
    assert descricao.sources.hf == vector_build._REPO_DO_MODELO_NO_HF
    assert descricao.model_file == vector_build._ARQUIVO_DO_MODELO


def test_indice_ainda_nao_pronto_nao_monta_as_tools_do_faq():
    from venus_sdk.tools.faq import montar_tools_faq

    class _IndiceEmConstrucao:
        pronto = False

        def buscar(self, consulta, k=3, score_minimo=None):
            raise AssertionError("não deveria buscar")

    with pytest.raises(ValueError, match="ainda não está pronto"):
        montar_tools_faq(_IndiceEmConstrucao())
