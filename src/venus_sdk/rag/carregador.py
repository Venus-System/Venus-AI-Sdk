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
    paginas = PdfReader(str(caminho)).pages
    return [(numero, pagina.extract_text() or "") for numero, pagina in enumerate(paginas, 1)]


def _ler_partes(caminho: Path) -> list[tuple[int | None, str]]:
    """`[(pagina, texto)]` do arquivo — `pagina` só existe em PDF."""
    if caminho.suffix.lower() == ".pdf":
        return _ler_pdf(caminho)
    return [(None, caminho.read_text(encoding="utf-8", errors="ignore"))]


def _arquivos_suportados(pasta: Path) -> list[Path]:
    return [
        caminho for caminho in sorted(pasta.rglob("*"))
        if caminho.suffix.lower() in EXTENSOES and caminho.is_file()
    ]


def carregar_documentos(pasta: str | Path, *, tamanho_chunk: int = 700, sobreposicao: int = 100) -> list[Document]:
    """Lê todos os arquivos suportados de `pasta` (recursivo) e devolve chunks
    `Document` com `metadata={"fonte": <arquivo>, "trecho": <n>, "pagina"?: <n>}`."""
    pasta = Path(pasta)
    if not pasta.is_dir():
        raise FileNotFoundError(f"Pasta de documentos do RAG não encontrada: {pasta}")
    divisor = RecursiveCharacterTextSplitter(chunk_size=tamanho_chunk, chunk_overlap=sobreposicao)
    chunks: list[Document] = []
    for caminho in _arquivos_suportados(pasta):
        numero_do_trecho = 0
        for pagina, texto in _ler_partes(caminho):
            for pedaco in divisor.split_text(texto):
                if not pedaco.strip():
                    continue
                numero_do_trecho += 1
                metadados = {"fonte": caminho.name, "trecho": numero_do_trecho}
                if pagina is not None:
                    metadados["pagina"] = pagina
                chunks.append(Document(page_content=pedaco, metadata=metadados))
    return chunks
