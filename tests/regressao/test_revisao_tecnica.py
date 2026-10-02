"""Regressão da revisão técnica (SDK em e91d4f1). Um bloco por item; cada
teste reproduz o problema descrito no item e afirma o comportamento certo."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest

from venus_sdk.nodes import especialistas as esp


def run(coro):
    return asyncio.run(coro)


# --- Item 1: o agente FAQ não pode derrubar o grafo -------------------------


def test_no_faq_sem_indice_vira_erro_tecnico_em_vez_de_excecao():
    no = esp.montar_no_agente_faq(None)
    saida = run(no({"pergunta_original": "o que é o venus?", "mensagem_usuario": "o que é o venus?"}))
    assert saida["resposta_especialista"]["intencao"] == "erro_tecnico"
    assert saida["evidencias_tools"] is None


def test_falha_ao_montar_o_agente_nao_fica_em_cache():
    tentativas = {"n": 0}

    def montar_tools():
        tentativas["n"] += 1
        if tentativas["n"] == 1:
            raise ValueError("índice ainda não carregado")
        return ["tool"]

    no = esp._montar_no_especialista("faq", "prompt", montar_tools)
    with patch.object(esp, "get_llm_especialista", return_value=object()), \
         patch.object(esp, "montar_agente_mcp", return_value="agente") as montar, \
         patch.object(esp, "_executar_especialista", side_effect=lambda e, n, a: {"agente": a}):
        primeira = run(no({"pergunta_original": "x"}))
        segunda = run(no({"pergunta_original": "x"}))
    assert primeira["resposta_especialista"]["intencao"] == "erro_tecnico"
    assert segunda == {"agente": "agente"} and montar.call_count == 1


def test_compilar_sem_indice_avisa_na_hora(caplog):
    from venus_sdk.flows.venus_flow import compilar_grafo_venus

    with caplog.at_level(logging.WARNING, logger="venus_sdk.flows.venus_flow"):
        compilar_grafo_venus()
    assert any("FAQ" in registro.getMessage() and "indisponível" in registro.getMessage()
               for registro in caplog.records)


class _EmbeddingsSemanticosFalsos:
    """Faz o papel do FastEmbed: textos sobre score ficam perto entre si."""

    def _vetor(self, texto: str) -> list[float]:
        return [1.0, 0.0] if "score" in texto.lower() else [0.0, 1.0]

    def embed_documents(self, textos: list[str]) -> list[list[float]]:
        return [self._vetor(t) for t in textos]

    def embed_query(self, texto: str) -> list[float]:
        return self._vetor(texto)


def test_indice_local_usa_embeddings_semanticos_quando_disponiveis(tmp_path, monkeypatch):
    from venus_sdk.rag import faq

    (tmp_path / "score.md").write_text("# Score\nO score do Venus vai de 0 a 100.", encoding="utf-8")
    monkeypatch.setattr(faq, "QDRANT_URL", None)
    monkeypatch.setattr(faq, "_embeddings_semanticos", lambda: _EmbeddingsSemanticosFalsos())
    indice = faq.criar_indice_faq(tmp_path)
    assert isinstance(indice.embeddings, _EmbeddingsSemanticosFalsos)
    assert indice.score_minimo == faq._SCORE_MINIMO
    assert indice.buscar("como funciona o score?")[0]["fonte"] == "score.md"


def test_sem_extra_rag_cai_no_hash_e_avisa(tmp_path, monkeypatch, caplog):
    from venus_sdk.rag import EmbeddingsHash, faq

    (tmp_path / "a.md").write_text("texto", encoding="utf-8")
    monkeypatch.setattr(faq, "QDRANT_URL", None)
    monkeypatch.setattr(faq, "_embeddings_semanticos", lambda: None)
    with caplog.at_level(logging.WARNING, logger="venus_sdk.rag.faq"):
        indice = faq.criar_indice_faq(tmp_path)
    assert isinstance(indice.embeddings, EmbeddingsHash)
    assert any("EmbeddingsHash" in r.getMessage() for r in caplog.records)


@pytest.fixture(autouse=True)
def _sem_qdrant(monkeypatch):
    from venus_sdk.rag import faq

    monkeypatch.setattr(faq, "QDRANT_URL", None)


# --- Item 2: o id do Postgres vem do uid do Firebase, nunca do cliente -------


def test_schema_tem_firebase_uid_unico_para_resolver_o_usuario():
    from pathlib import Path

    schema = (Path(__file__).resolve().parents[2] / "scripts" / "sql" / "schema.sql").read_text(encoding="utf-8")
    assert "firebase_uid TEXT UNIQUE" in schema
    # Bancos já criados recebem a coluna sem precisar recriar a tabela.
    assert "ADD COLUMN IF NOT EXISTS firebase_uid TEXT UNIQUE" in schema
