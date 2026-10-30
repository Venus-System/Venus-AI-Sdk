"""O conjunto de avaliação do roteador (`tests/fixtures/avaliacao_roteamento.jsonl`)
está bem formado e o script manual (`tests/manual/avaliar_roteamento.py`) roda
sem LLM. A taxa de acerto com o LLM de verdade fica em `docs/avaliacao-roteamento.md`."""

import importlib.util
from collections import Counter
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
_ROTAS = {"produto", "ingrediente", "rotina", "faq", "direto"}


@pytest.fixture(scope="module")
def avaliar_roteamento():
    spec = importlib.util.spec_from_file_location(
        "avaliar_roteamento", RAIZ / "tests" / "manual" / "avaliar_roteamento.py"
    )
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_conjunto_bem_formado(avaliar_roteamento):
    casos = avaliar_roteamento.carregar()
    perguntas = [caso["pergunta"] for caso in casos]
    assert len(perguntas) == len(set(perguntas))
    assert all(caso["rotas"] and set(caso["rotas"]) <= _ROTAS for caso in casos)
    por_categoria = Counter(caso["categoria"] for caso in casos)
    assert set(por_categoria) == set(avaliar_roteamento.CATEGORIAS)
    assert min(por_categoria.values()) >= 4
    assert all(len(caso["rotas"]) >= 2 for caso in casos if caso["categoria"] == "mista")


def test_alergia_no_chat_direta_so_vale_com_a_resposta_fixa(avaliar_roteamento):
    caso = {"pergunta": "sou alérgica a lavanda", "rotas": ["direto", "rotina"], "categoria": "alergia_no_chat"}
    assert not avaliar_roteamento.acertou(caso, "direto", "Anotado, vou lembrar!")
    assert avaliar_roteamento.acertou(caso, "direto", avaliar_roteamento._RESPOSTA_ALERGIA_NO_CHAT)
    assert avaliar_roteamento.acertou(caso, "rotina", None)


def test_roda_so_com_palavras_chave(avaliar_roteamento, capsys):
    assert avaliar_roteamento.main(["--sem-llm"]) == 0
    saida = capsys.readouterr().out
    assert "| Categoria | palavras-chave |" in saida and "**total**" in saida
