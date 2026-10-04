"""Item 5 da revisão técnica 3: seções em forma de pergunta para os casos que o
RAG recuperava mal, e comentários de manutenção (TODO) fora do índice."""

import json
import re
from pathlib import Path

import pytest

from venus_sdk.config.settings import FAQ_DIR
from venus_sdk.rag import carregar_documentos

FAQ = Path(FAQ_DIR)
RAIZ = Path(__file__).resolve().parents[2]


def _secoes(arquivo: str) -> dict[str, str]:
    texto = (FAQ / arquivo).read_text(encoding="utf-8")
    partes = re.split(r"^## (.+)$", texto, flags=re.M)
    return {titulo.strip(): corpo for titulo, corpo in zip(partes[1::2], partes[2::2])}


def test_privacidade_responde_direto_se_o_venus_vende_os_dados():
    corpo = _secoes("privacidade_e_dados.md")["O Venus vende meus dados?"]
    assert "não vende" in corpo


def test_privacidade_responde_o_que_acontece_com_o_cpf_no_chat():
    corpo = _secoes("privacidade_e_dados.md")["O que acontece se eu digitar meu CPF no chat?"]
    assert "anonimizados" in corpo


def test_autoria_nao_inventada_fica_como_todo_e_sem_resposta():
    texto = (FAQ / "sobre_o_venus.md").read_text(encoding="utf-8")
    assert "TODO" in texto and not re.search(r"^## Quem criou", texto, re.M)
    perguntas = [json.loads(linha) for linha in
                 (RAIZ / "tests/fixtures/avaliacao_rag.jsonl").read_text(encoding="utf-8").splitlines()]
    assert next(p for p in perguntas if p["pergunta"] == "quem criou o aplicativo?")["fontes"] == []


def test_comentario_html_nao_entra_no_indice_local(tmp_path):
    (tmp_path / "doc.md").write_text(
        "# Doc\n\n<!-- TODO(produto): escrever quem criou -->\n\n## Seção\n\nTexto visível.\n<!--\nvárias\nlinhas\n-->\n",
        encoding="utf-8",
    )
    conteudo = " ".join(doc.page_content for doc in carregar_documentos(tmp_path))
    assert "Texto visível." in conteudo
    assert "TODO" not in conteudo and "várias" not in conteudo and "<!--" not in conteudo


def test_nenhum_trecho_do_faq_oficial_carrega_o_todo():
    assert not any("TODO" in doc.page_content for doc in carregar_documentos(FAQ))


def test_comentario_html_nao_entra_no_qdrant():
    pytest.importorskip("qdrant_client")
    pytest.importorskip("llama_index.core")
    from qdrant_client import QdrantClient

    from venus_sdk.rag.faq_ingest import ingerir_faq
    from venus_sdk.rag.vector_build import COLLECTION

    from tools.test_rag_qdrant import _EmbedHash

    cliente = QdrantClient(":memory:")
    ingerir_faq(FAQ, cliente=cliente, embed_model=_EmbedHash())
    pontos, _ = cliente.scroll(COLLECTION, limit=1000, with_payload=True)
    assert pontos and not any("TODO" in json.dumps(p.payload, ensure_ascii=False) for p in pontos)
