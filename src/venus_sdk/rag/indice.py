"""Índice vetorial local (numpy, similaridade do cosseno).

Embeddings são injetáveis: por padrão `EmbeddingsHash` (offline, determinístico,
sem chave de API — bag-of-words com hashing); com `GEMINI_API_KEY` dá para
passar `GoogleGenerativeAIEmbeddings`. O índice é reconstruído a partir da
pasta e cacheado em disco (`.npz`), invalidado quando os arquivos mudam.
Alternativa remota (Qdrant) fica a cargo de quem injetar outro `IndiceRAG`."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from venus_sdk.rag.carregador import EXTENSOES, carregar_documentos
from venus_sdk.texto import remover_acentos

_PALAVRAS_VAZIAS = {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "um", "uma", "que", "para",
                    "por", "com", "no", "na", "nos", "nas", "se", "ao", "como", "qual", "quais", "sao",
                    "venus", "meu", "meus", "minha", "minhas", "seu", "seus", "sua", "suas", "ser", "tem"}
_PALAVRA_RE = re.compile(r"[a-z0-9]+")
# Radical grosseiro (5 primeiras letras): "calcula"/"calculo"/"calcular",
# "cadastro"/"cadastrar" caem no mesmo token sem precisar de stemmer.
_TAMANHO_RADICAL = 5
_CASAS_DECIMAIS_SCORE = 3


def _tokens(texto: str) -> list[str]:
    palavras = _PALAVRA_RE.findall(remover_acentos(texto.lower()))
    return [palavra[:_TAMANHO_RADICAL] for palavra in palavras if palavra not in _PALAVRAS_VAZIAS and len(palavra) > 1]


class EmbeddingsHash(Embeddings):
    """Embedding offline: hashing de unigramas e bigramas em `dim` dimensões."""

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def _vetor(self, texto: str) -> list[float]:
        vetor = np.zeros(self.dim, dtype=np.float32)
        tokens = _tokens(texto)
        bigramas = [f"{anterior}_{atual}" for anterior, atual in zip(tokens, tokens[1:])]
        for grama in tokens + bigramas:
            posicao = int(hashlib.md5(grama.encode()).hexdigest(), 16) % self.dim
            vetor[posicao] += 1.0
        norma = float(np.linalg.norm(vetor))
        return (vetor / norma).tolist() if norma else vetor.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vetor(texto) for texto in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vetor(text)


class IndiceRAG:
    """Índice vetorial em memória sobre uma lista de `Document`."""

    def __init__(self, documentos: list[Document], embeddings: Embeddings | None = None,
                 matriz: np.ndarray | None = None) -> None:
        self.embeddings = embeddings or EmbeddingsHash()
        self.documentos = documentos
        self.matriz = matriz if matriz is not None else self._calcular_matriz()

    def _calcular_matriz(self) -> np.ndarray:
        if not self.documentos:
            return np.zeros((0, 1), dtype=np.float32)
        vetores = self.embeddings.embed_documents([documento.page_content for documento in self.documentos])
        return np.array(vetores, dtype=np.float32)

    def buscar(self, consulta: str, k: int = 3, score_minimo: float = 0.1) -> list[dict[str, Any]]:
        """Top-k chunks por cosseno. Devolve `[{trecho, fonte, score, ...}]`
        (vazio se nada passar de `score_minimo` — o agente deve então dizer
        que não sabe, nunca inventar)."""
        if not self.documentos:
            return []
        vetor_consulta = np.array(self.embeddings.embed_query(consulta), dtype=np.float32)
        norma_consulta = float(np.linalg.norm(vetor_consulta))
        if not norma_consulta:
            return []
        normas = np.linalg.norm(self.matriz, axis=1)
        normas[normas == 0] = 1.0
        scores = (self.matriz @ vetor_consulta) / (normas * norma_consulta)
        melhores = np.argsort(-scores)[:k]
        return [self._resultado(int(i), float(scores[i])) for i in melhores if scores[i] >= score_minimo]

    def _resultado(self, indice: int, score: float) -> dict[str, Any]:
        documento = self.documentos[indice]
        resultado = {
            "trecho": documento.page_content,
            "fonte": documento.metadata.get("fonte"),
            "score": round(score, _CASAS_DECIMAIS_SCORE),
        }
        if "pagina" in documento.metadata:
            resultado["pagina"] = documento.metadata["pagina"]
        return resultado


def _assinatura(pasta: Path) -> str:
    """Hash dos arquivos indexáveis (caminho, tamanho, data): muda quando algum muda."""
    itens = [
        (str(caminho.relative_to(pasta)), caminho.stat().st_size, int(caminho.stat().st_mtime))
        for caminho in sorted(pasta.rglob("*"))
        if caminho.is_file() and caminho.suffix.lower() in EXTENSOES
    ]
    return hashlib.md5(json.dumps(itens).encode()).hexdigest()


def criar_indice_local(pasta: str | Path, embeddings: Embeddings | None = None, *,
                       cache: str | Path | None = None) -> IndiceRAG:
    """Constrói o índice a partir de `pasta`. Com `cache` (arquivo .npz) e
    embeddings padrão, reaproveita a matriz enquanto os arquivos não mudarem."""
    pasta = Path(pasta)
    documentos = carregar_documentos(pasta)
    usa_cache = bool(cache) and embeddings is None
    if not usa_cache:
        return IndiceRAG(documentos, embeddings)

    arquivo_cache = Path(cache)
    assinatura = _assinatura(pasta)
    matriz = _matriz_do_cache(arquivo_cache, assinatura, len(documentos))
    if matriz is not None:
        return IndiceRAG(documentos, None, matriz)

    indice = IndiceRAG(documentos, embeddings)
    arquivo_cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(arquivo_cache, assinatura=np.array(assinatura), matriz=indice.matriz)
    return indice


def _matriz_do_cache(arquivo_cache: Path, assinatura: str, total_documentos: int) -> np.ndarray | None:
    """Matriz salva, se o cache existir e ainda corresponder aos arquivos atuais."""
    if not arquivo_cache.exists():
        return None
    dados = np.load(arquivo_cache, allow_pickle=False)
    if str(dados["assinatura"]) == assinatura and dados["matriz"].shape[0] == total_documentos:
        return dados["matriz"]
    return None
