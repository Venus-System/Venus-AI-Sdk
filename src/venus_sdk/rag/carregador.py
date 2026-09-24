"""Carrega documentos locais (.md, .txt, .pdf) e os divide em chunks com
metadados de fonte (arquivo + trecho/página)."""

from __future__ import annotations

from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

EXTENSOES = {".md", ".txt", ".pdf"}


def _ler_pdf(caminho: Path) -> list[tuple[int, str]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise ImportError("Para indexar PDFs instale `pypdf` (pip install pypdf).") from exc
    return [(n, p.extract_text() or "") for n, p in enumerate(PdfReader(str(caminho)).pages, 1)]


def carregar_documentos(pasta: str | Path, *, tamanho_chunk: int = 700, sobreposicao: int = 100) -> list[Document]:
    """Lê todos os arquivos suportados de `pasta` (recursivo) e devolve chunks
    `Document` com `metadata={"fonte": <arquivo>, "trecho": <n>, "pagina"?: <n>}`."""
    pasta = Path(pasta)
    if not pasta.is_dir():
        raise FileNotFoundError(f"Pasta de documentos do RAG não encontrada: {pasta}")
    divisor = RecursiveCharacterTextSplitter(chunk_size=tamanho_chunk, chunk_overlap=sobreposicao)
    chunks: list[Document] = []
    for caminho in sorted(pasta.rglob("*")):
        if caminho.suffix.lower() not in EXTENSOES or not caminho.is_file():
            continue
        if caminho.suffix.lower() == ".pdf":
            partes = [(pg, txt) for pg, txt in _ler_pdf(caminho)]
        else:
            partes = [(None, caminho.read_text(encoding="utf-8", errors="ignore"))]
        n = 0
        for pagina, texto in partes:
            for pedaco in divisor.split_text(texto):
                if not pedaco.strip():
                    continue
                n += 1
                meta = {"fonte": caminho.name, "trecho": n}
                if pagina is not None:
                    meta["pagina"] = pagina
                chunks.append(Document(page_content=pedaco, metadata=meta))
    return chunks
