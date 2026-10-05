"""Conexão com o Qdrant e modelo de embeddings do FAQ (extra `rag`).

Tudo é criado sob demanda (nada no import): o SDK continua importável sem as
dependências do extra `rag` e sem `QDRANT_URL`, e cada processo da API abre
um único cliente e carrega o modelo uma única vez."""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from venus_sdk.config.settings import QDRANT_API_KEY, QDRANT_URL

logger = logging.getLogger(__name__)

COLLECTION = "faq_chunks"

# Modelo multilíngue do FastEmbed (~50 línguas, inclui português; roda local,
# sem chave de API): 384 dimensões, 0,22 GB — leve o bastante para cada
# instância da API carregar. Ingestão e busca PRECISAM usar o mesmo modelo
# (trocar o modelo exige rodar a ingestão de novo).
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# Onde o FastEmbed guarda esse modelo no cache (conferido contra os metadados
# da biblioteca em tests/tools/test_fastembed_sem_rede.py).
_REPO_DO_MODELO_NO_HF = "qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
_ARQUIVO_DO_MODELO = "model_optimized.onnx"
# Sem o modelo em cache, o FastEmbed tenta baixar 3 vezes, esperando 3, 9 e
# 27 s entre elas: sem rede, a API levava ~40 s para subir. Limitamos o
# download a um tempo total e, sem permissão de download, nem tentamos.
_TIMEOUT_DOWNLOAD_PADRAO_SEGUNDOS = 15.0


class ModeloIndisponivel(RuntimeError):
    """O modelo de embeddings não está em cache e não pôde ser baixado (download
    proibido, offline ou tempo esgotado). Quem chama cai no `EmbeddingsHash`."""


@lru_cache(maxsize=1)
def get_qdrant_client() -> Any:
    """Cliente do Qdrant apontando para `QDRANT_URL` (+ `QDRANT_API_KEY`)."""
    if not QDRANT_URL:
        raise ValueError("QDRANT_URL não configurada — defina no .env para usar o FAQ no Qdrant.")
    from qdrant_client import QdrantClient

    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


def pasta_do_cache() -> Path:
    """Cache do FastEmbed: `FASTEMBED_CACHE_PATH` ou o padrão da biblioteca."""
    return Path(os.getenv("FASTEMBED_CACHE_PATH") or Path(tempfile.gettempdir()) / "fastembed_cache")


def modelo_em_cache(pasta: Path | None = None) -> bool:
    """O arquivo do modelo já está no cache? Olha o disco, sem importar a
    biblioteca (que sozinha leva alguns segundos para carregar)."""
    snapshots = (pasta or pasta_do_cache()) / f"models--{_REPO_DO_MODELO_NO_HF.replace('/', '--')}" / "snapshots"
    return any(snapshots.glob(f"*/{_ARQUIVO_DO_MODELO}"))


def _download_permitido() -> bool:
    """`VENUS_FASTEMBED_DOWNLOAD=0` proíbe baixar (usa só o cache);
    `HF_HUB_OFFLINE=1` também — a biblioteca já não iria à rede."""
    if os.getenv("HF_HUB_OFFLINE", "").strip().upper() in {"1", "TRUE", "YES", "ON"}:
        return False
    return os.getenv("VENUS_FASTEMBED_DOWNLOAD", "1").strip() != "0"


def _timeout_do_download() -> float:
    try:
        return float(os.getenv("VENUS_FASTEMBED_TIMEOUT_SEGUNDOS") or _TIMEOUT_DOWNLOAD_PADRAO_SEGUNDOS)
    except ValueError:
        return _TIMEOUT_DOWNLOAD_PADRAO_SEGUNDOS


def _criar_fastembed(*, somente_local: bool) -> Any:
    from llama_index.embeddings.fastembed import FastEmbedEmbedding

    if somente_local:
        return FastEmbedEmbedding(model_name=EMBEDDING_MODEL_NAME, local_files_only=True)
    return FastEmbedEmbedding(model_name=EMBEDDING_MODEL_NAME)


def _baixar_com_tempo_maximo(segundos: float) -> Any:
    """A biblioteca não aceita limite de tempo para as tentativas, então a
    criação roda numa thread e esperamos no máximo `segundos`. Thread daemon:
    se o tempo estourar, o download segue em segundo plano sem segurar o
    encerramento do processo."""
    resultado: dict[str, Any] = {}

    def criar() -> None:
        try:
            resultado["modelo"] = _criar_fastembed(somente_local=False)
        except Exception as erro:  # noqa: BLE001 — repassado abaixo
            resultado["erro"] = erro

    thread = threading.Thread(target=criar, name="download-fastembed", daemon=True)
    thread.start()
    thread.join(segundos)
    if thread.is_alive():
        raise ModeloIndisponivel(f"download do modelo do FastEmbed passou de {segundos:g} s")
    if "erro" in resultado:
        raise ModeloIndisponivel(f"download do modelo do FastEmbed falhou ({type(resultado['erro']).__name__})") \
            from resultado["erro"]
    return resultado["modelo"]


@lru_cache(maxsize=1)
def get_embed_model() -> Any:
    """Modelo de embeddings do FastEmbed. Com o modelo no cache
    (`FASTEMBED_CACHE_PATH`), carrega sem ir à rede; sem ele, baixa (~220 MB)
    por no máximo `VENUS_FASTEMBED_TIMEOUT_SEGUNDOS` (15 s), ou levanta
    `ModeloIndisponivel` na hora se o download estiver proibido
    (`VENUS_FASTEMBED_DOWNLOAD=0` ou `HF_HUB_OFFLINE=1`)."""
    if modelo_em_cache():
        return _criar_fastembed(somente_local=True)
    if not _download_permitido():
        raise ModeloIndisponivel(
            f"modelo do FastEmbed fora do cache ({pasta_do_cache()}) e download desligado"
        )
    return _baixar_com_tempo_maximo(_timeout_do_download())


def get_vector_store(cliente: Any | None = None) -> Any:
    """Vector store do llama_index sobre a coleção do FAQ."""
    from llama_index.vector_stores.qdrant import QdrantVectorStore

    return QdrantVectorStore(client=cliente or get_qdrant_client(), collection_name=COLLECTION)
