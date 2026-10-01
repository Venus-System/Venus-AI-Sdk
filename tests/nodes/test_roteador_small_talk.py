"""Rede de segurança do roteador: saudação pura nunca vai para um especialista."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from _fakes import LLMScript
from langchain_core.messages import AIMessage

from venus_sdk.nodes.roteador import decidir_especialista, e_small_talk, no_roteador


@pytest.mark.parametrize("msg", ["oi", "Oi!", "olá", "bom dia", "boa noite, tudo bem?", "obrigada", "valeu!", "tchau"])
def test_saudacoes_sao_small_talk(msg: str) -> None:
    assert e_small_talk(msg)


@pytest.mark.parametrize("msg", [
    "quem é o presidente?", "oi, monta uma rotina pra mim", "niacinamida faz mal?",
    "bom dia, qual protetor solar você recomenda", "obrigada, e o retinol?",
])
def test_pedidos_reais_nao_sao_small_talk(msg: str) -> None:
    assert not e_small_talk(msg)


@pytest.mark.parametrize("msg", ["oi", "bom dia", "obrigada"])
@pytest.mark.parametrize("rota_errada", ["rotina", "produto", "ingrediente", "faq"])
def test_llm_que_roteia_saudacao_para_especialista_e_corrigido(msg: str, rota_errada: str) -> None:
    llm = LLMScript(script=[AIMessage(content=f"ROUTE={rota_errada}\nPERGUNTA_ORIGINAL={msg}")])
    with patch("venus_sdk.nodes.roteador.get_llm_rapido", return_value=llm):
        estado = no_roteador({"mensagem_usuario": msg, "historico": []})
    assert decidir_especialista(estado) == "direto"
    assert estado["resposta_final"]


def test_pedido_real_continua_roteado() -> None:
    llm = LLMScript(script=[AIMessage(content="ROUTE=rotina\nPERGUNTA_ORIGINAL=monta uma rotina")])
    with patch("venus_sdk.nodes.roteador.get_llm_rapido", return_value=llm):
        estado = no_roteador({"mensagem_usuario": "monta uma rotina", "historico": []})
    assert decidir_especialista(estado) == "rotina"


from venus_sdk.nodes.roteador import rota_por_palavras  # noqa: E402


def test_rota_por_palavras() -> None:
    assert rota_por_palavras("monta uma rotina de manhã pra mim") == "rotina"
    assert rota_por_palavras("o que é niacinamida?") == "ingrediente"
    assert rota_por_palavras("produto bom pra cabelo cacheado") == "produto"
    assert rota_por_palavras("escreve um código em python") is None
    assert rota_por_palavras("oi, tudo bem?") is None


def test_rota_por_palavras_faq() -> None:
    assert rota_por_palavras("como funciona o score do Venus?") == "faq"
    assert rota_por_palavras("vocês guardam meus dados pessoais?") == "faq"
    assert rota_por_palavras("qual o score do produto CeraVe?") == "produto"


def test_resposta_direta_com_fatos_de_produto_e_substituida() -> None:
    from unittest.mock import patch

    from _fakes import LLMScript
    from langchain_core.messages import AIMessage

    from venus_sdk.nodes.roteador import no_roteador

    llm = LLMScript(script=[AIMessage(content="Oii, Marina! O CeraVe tem score 82/100 e não contém fragrância.")])
    with patch("venus_sdk.nodes.roteador.get_llm_rapido", return_value=llm):
        r = no_roteador({"mensagem_usuario": "meu nome é Marina e minha pele é sensível", "historico": []})
    assert r["rota"] is None and "82" not in r["resposta_final"] and "Anotei" in r["resposta_final"]
