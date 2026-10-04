"""Carrega documentos locais (.md, .txt, .pdf) e os divide em chunks com
metadados de fonte (arquivo + trecho/página; seção, no markdown)."""

from __future__ import annotations

import re
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

EXTENSOES = {".md", ".txt", ".pdf"}
# Entra na assinatura do cache do índice local (`indice._assinatura`): mudar a
# divisão invalida as matrizes salvas mesmo sem mudar os arquivos.
VERSAO_DA_DIVISAO = 2
_TITULOS = [("#", "h1"), ("##", "h2"), ("###", "h3")]
# Comentário HTML num .md (ex.: `<!-- TODO(produto): ... -->`) é nota para
# quem mantém o FAQ, não conteúdo: nunca vai para o índice nem para o agente.
_COMENTARIO_HTML = re.compile(r"<!--.*?-->", re.S)


def remover_comentarios_html(texto: str) -> str:
    """Tira os comentários HTML do markdown antes de indexar."""
    return _COMENTARIO_HTML.sub("", texto)


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
    texto = caminho.read_text(encoding="utf-8", errors="ignore")
    if caminho.suffix.lower() == ".md":
        texto = remover_comentarios_html(texto)
    return [(None, texto)]


def _secoes_do_markdown(texto: str) -> list[tuple[str, str]]:
    """`[(caminho de títulos, texto da seção)]`. O caminho começa pelo título
    do documento (o primeiro `# `), que se repete em todas as seções: um
    trecho do meio de um documento longo continua dizendo de onde veio."""
    titulo = next((linha[2:].strip() for linha in texto.splitlines() if linha.startswith("# ")), "")
    secoes = []
    for secao in MarkdownHeaderTextSplitter(_TITULOS, strip_headers=True).split_text(texto):
        caminho = [titulo] if titulo else []
        caminho += [secao.metadata[nivel] for _, nivel in _TITULOS
                    if secao.metadata.get(nivel) and secao.metadata[nivel] != titulo]
        secoes.append((" > ".join(caminho), secao.page_content))
    return secoes


def _arquivos_suportados(pasta: Path) -> list[Path]:
    return [
        caminho for caminho in sorted(pasta.rglob("*"))
        if caminho.suffix.lower() in EXTENSOES and caminho.is_file()
    ]


def carregar_documentos(pasta: str | Path, *, tamanho_chunk: int = 700, sobreposicao: int = 100) -> list[Document]:
    """Lê todos os arquivos suportados de `pasta` (recursivo) e devolve chunks
    `Document` com `metadata={"fonte": <arquivo>, "trecho": <n>, "pagina"?: <n>,
    "secao"?: <títulos>}`.

    Markdown é dividido primeiro por seção (`#`, `##`, `###`) e só depois por
    tamanho, e cada pedaço começa com o caminho de títulos: um trecho nunca
    mistura duas seções, e o embedding sabe do que ele trata. Sem isso, o
    `api_de_classificacao.md` (739 linhas, tabelas e código) virava pedaços
    sem contexto que ganhavam de documentos de outros assuntos."""
    pasta = Path(pasta)
    if not pasta.is_dir():
        raise FileNotFoundError(f"Pasta de documentos do RAG não encontrada: {pasta}")
    divisor = RecursiveCharacterTextSplitter(chunk_size=tamanho_chunk, chunk_overlap=sobreposicao)
    chunks: list[Document] = []
    for caminho in _arquivos_suportados(pasta):
        numero_do_trecho = 0
        for pagina, texto in _ler_partes(caminho):
            secoes = _secoes_do_markdown(texto) if caminho.suffix.lower() == ".md" else [("", texto)]
            for secao, texto_da_secao in secoes:
                for pedaco in divisor.split_text(texto_da_secao):
                    if not pedaco.strip():
                        continue
                    numero_do_trecho += 1
                    metadados = {"fonte": caminho.name, "trecho": numero_do_trecho}
                    if pagina is not None:
                        metadados["pagina"] = pagina
                    if secao:
                        metadados["secao"] = secao
                        pedaco = f"{secao}\n\n{pedaco}"
                    chunks.append(Document(page_content=pedaco, metadata=metadados))
    return chunks
