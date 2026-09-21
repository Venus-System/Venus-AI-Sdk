"""Índice vetorial local (numpy, similaridade do cosseno).

Embeddings são injetáveis: por padrão `EmbeddingsHash` (offline, determinístico,
sem chave de API — bag-of-words com hashing); com `GEMINI_API_KEY` dá para
passar `GoogleGenerativeAIEmbeddings`. O índice é reconstruído a partir da
pasta e cacheado em disco (`.npz`), invalidado quando os arquivos mudam.
Alternativa remota (Qdrant) fica a cargo de quem injetar outro `IndiceRAG`."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from venus_sdk.rag.carregador import EXTENSOES, carregar_documentos

_STOP = {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "um", "uma", "que", "para",
         "por", "com", "no", "na", "nos", "nas", "se", "ao", "como", "qual", "quais", "sao",
         "venus", "meu", "meus", "minha", "minhas", "seu", "seus", "sua", "suas", "sao", "ser", "tem"}


def _tokens(texto: str) -> list[str]:
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    palavras = [w for w in re.findall(r"[a-z0-9]+", t) if w not in _STOP and len(w) > 1]
    # Radical grosseiro (5 primeiras letras): "calcula"/"calculo"/"calcular",
    # "cadastro"/"cadastrar" caem no mesmo token sem precisar de stemmer.
    return [w[:5] for w in palavras]


class EmbeddingsHash(Embeddings):
    """Embedding offline: hashing de unigramas e bigramas em `dim` dimensões."""

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def _vec(self, texto: str) -> list[float]:
        v = np.zeros(self.dim, dtype=np.float32)
        toks = _tokens(texto)
        for gram in toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]:
            h = int(hashlib.md5(gram.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0
        n = float(np.linalg.norm(v))
        return (v / n).tolist() if n else v.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class IndiceRAG:
    """Índice vetorial em memória sobre uma lista de `Document`."""

    def __init__(self, documentos: list[Document], embeddings: Embeddings | None = None,
                 matriz: np.ndarray | None = None) -> None:
        self.embeddings = embeddings or EmbeddingsHash()
        self.documentos = documentos
        if matriz is None:
            matriz = np.array(self.embeddings.embed_documents([d.page_content for d in documentos]), dtype=np.float32) \
                if documentos else np.zeros((0, 1), dtype=np.float32)
        self.matriz = matriz

    def buscar(self, consulta: str, k: int = 3, score_minimo: float = 0.1) -> list[dict[str, Any]]:
        """Top-k chunks por cosseno. Devolve `[{trecho, fonte, score, ...}]`
        (vazio se nada passar de `score_minimo` — o agente deve então dizer
        que não sabe, nunca inventar)."""
        if not len(self.documentos):
            return []
        q = np.array(self.embeddings.embed_query(consulta), dtype=np.float32)
        nq = float(np.linalg.norm(q))
        if not nq:
            return []
        normas = np.linalg.norm(self.matriz, axis=1)
        normas[normas == 0] = 1.0
        scores = (self.matriz @ q) / (normas * nq)
        ordem = np.argsort(-scores)[:k]
        saida = []
        for i in ordem:
            if scores[i] < score_minimo:
                continue
            d = self.documentos[int(i)]
            item = {"trecho": d.page_content, "fonte": d.metadata.get("fonte"), "score": round(float(scores[i]), 3)}
            if "pagina" in d.metadata:
                item["pagina"] = d.metadata["pagina"]
            saida.append(item)
        return saida


def _assinatura(pasta: Path) -> str:
    itens = [(str(p.relative_to(pasta)), p.stat().st_size, int(p.stat().st_mtime))
             for p in sorted(pasta.rglob("*")) if p.is_file() and p.suffix.lower() in EXTENSOES]
    return hashlib.md5(json.dumps(itens).encode()).hexdigest()


def criar_indice_local(pasta: str | Path, embeddings: Embeddings | None = None, *,
                       cache: str | Path | None = None) -> IndiceRAG:
    """Constrói o índice a partir de `pasta`. Com `cache` (arquivo .npz) e
    embeddings padrão, reaproveita a matriz enquanto os arquivos não mudarem."""
    pasta = Path(pasta)
    docs = carregar_documentos(pasta)
    assinatura = _assinatura(pasta)
    if cache and embeddings is None:
        cache = Path(cache)
        if cache.exists():
            dados = np.load(cache, allow_pickle=False)
            if str(dados["assinatura"]) == assinatura and dados["matriz"].shape[0] == len(docs):
                return IndiceRAG(docs, None, dados["matriz"])
    indice = IndiceRAG(docs, embeddings)
    if cache and embeddings is None:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache, assinatura=np.array(assinatura), matriz=indice.matriz)
    return indice
