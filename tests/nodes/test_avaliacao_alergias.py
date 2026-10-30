"""Avaliação do casamento alergia × ingrediente (`tests/manual/avaliar_alergias.py`):
não usa LLM nem banco, então roda aqui. Falha se aparecer um erro fora das
lacunas marcadas `"lacuna_conhecida": true` no conjunto — e também quando uma
lacuna é corrigida, para o conjunto ser atualizado junto."""

import importlib.util
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def avaliar_alergias():
    spec = importlib.util.spec_from_file_location("avaliar_alergias", RAIZ / "tests" / "manual" / "avaliar_alergias.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def relatorio(avaliar_alergias):
    return avaliar_alergias.avaliar(avaliar_alergias.carregar())


def _chaves(casos):
    return {(caso["alergia"], caso["inci"]) for caso in casos}


def test_erros_do_casamento_sao_exatamente_as_lacunas_conhecidas(avaliar_alergias, relatorio):
    lacunas = _chaves(caso for caso in avaliar_alergias.carregar() if caso.get("lacuna_conhecida"))
    assert _chaves(relatorio["perdidos"] + relatorio["falsos"]) == lacunas


def test_alerta_na_resposta_final_segue_o_casamento(relatorio):
    # O alerta ponta a ponta só erra onde o casamento erra: o Orquestrador não perde nenhum.
    assert _chaves(relatorio["alerta_errado"]) == _chaves(relatorio["perdidos"] + relatorio["falsos"])


def test_alcoois_graxos_nunca_viram_conflito(relatorio):
    assert not [caso for caso in relatorio["falsos"] if caso["alergia"] == "Álcool"]


def test_catalogo_de_producao_sem_erro(avaliar_alergias):
    # As 18 alergias de venus.allergies × os ingredientes usados em produtos, com
    # gabarito escrito sem `alergias.py`. Antes da correção de 2026-10-30: 53/71
    # conflitos achados e 5 falsos (Parfum, propilenoglicol, metossulfato...).
    catalogo = avaliar_alergias.avaliar_catalogo()
    assert catalogo["pares"] > 9000
    assert catalogo["perdidos"] == [] and catalogo["falsos"] == []


def test_intervalo_de_confianca(avaliar_alergias):
    baixo, alto = avaliar_alergias.intervalo(47, 47)
    assert alto == 1.0 and 0.92 < baixo < 0.93


def test_script_imprime_a_tabela(avaliar_alergias, capsys):
    assert avaliar_alergias.main([]) == 0
    saida = capsys.readouterr().out
    assert "Conflitos achados (recall)" in saida and "IC 95%" in saida and "Catálogo de produção" in saida
