"""Perguntas sobre o próprio Venus vão para o FAQ mesmo quando o LLM roteador
responde direto: ele respondia "como apago minha conta?" ou "de onde vêm os
dados?" sozinho, às vezes inventando (ver docs/avaliacao-roteamento.md)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from venus_sdk.nodes.roteador import no_roteador, rota_por_palavras


@pytest.mark.parametrize(
    "mensagem",
    [
        "como eu apago minha conta?",
        "quero excluir minha conta",
        "como crio uma conta?",
        "como a nota dos produtos é calculada?",
        "como funciona a pontuação?",
        "de onde vêm os dados dos ingredientes?",
        "quais as fontes dos dados?",
        "o venus substitui um dermatologista?",
        "isso substitui uma consulta médica?",
        "posso confiar na IA se tenho alergia?",
        "é seguro mandar meu CPF aqui?",
    ],
)
def test_pergunta_sobre_o_venus_vai_para_faq(mensagem: str) -> None:
    assert rota_por_palavras(mensagem) == "faq"


@pytest.mark.parametrize(
    "mensagem",
    [
        "de onde vem esse cheiro forte do meu shampoo?",
        "esse protetor substitui o hidratante?",
        "quais são meus favoritos?",
        "tira esse creme da minha lista",
        "qual a nota do Hidratante Facial CeraVe?",
    ],
)
def test_outras_perguntas_nao_caem_no_faq(mensagem: str) -> None:
    assert rota_por_palavras(mensagem) != "faq"


def test_resposta_direta_inventada_sobre_o_venus_vira_rota_faq() -> None:
    with patch("venus_sdk.nodes.roteador.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = SimpleNamespace(
            content="Oii!! Os dados vêm de bancos científicos e da FDA, tudo checado!"
        )
        resultado = no_roteador({"mensagem_usuario": "de onde vêm os dados dos ingredientes?"})
    assert resultado["rota"] == "faq"
    assert "resposta_final" not in resultado
