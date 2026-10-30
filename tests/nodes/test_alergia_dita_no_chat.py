"""Alergia contada no chat: a memória não a guarda (a fonte é o cadastro), então
a resposta direta do roteador manda atualizar a lista de alergias no app em vez
de prometer "vou lembrar" — ver `nodes/roteador.py::_RESPOSTA_ALERGIA_NO_CHAT`."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from venus_sdk.nodes.roteador import _RESPOSTA_ALERGIA_NO_CHAT, no_roteador
from venus_sdk.prompts.faq import FAQ_PROMPT_COMPLETO
from venus_sdk.prompts.ingrediente import ESP_INGREDIENTE_PROMPT_COMPLETO
from venus_sdk.prompts.produto import ESP_PRODUTO_PROMPT_COMPLETO
from venus_sdk.prompts.rotina import ROTINA_PROMPT_COMPLETO


def _rotear(mensagem: str, texto_llm: str) -> dict:
    with patch("venus_sdk.nodes.roteador.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = SimpleNamespace(content=texto_llm)
        return no_roteador({"mensagem_usuario": mensagem})


@pytest.mark.parametrize(
    "mensagem",
    [
        "descobri que sou alérgica a óleo essencial de lavanda",
        "tenho alergia a lavanda",
        "não sou mais alérgico a sulfato",
        "fiquei sensível a perfume",
        "perfume me dá alergia",
    ],
)
def test_resposta_direta_a_alergia_manda_atualizar_o_cadastro(mensagem: str) -> None:
    resultado = _rotear(mensagem, "Anotado! Vou lembrar disso nas próximas conversas.")
    assert resultado["rota"] is None
    assert resultado["resposta_final"] == _RESPOSTA_ALERGIA_NO_CHAT
    assert resultado["expressao"] == "neutra"


def test_alergia_a_ingrediente_conhecido_vai_ao_especialista_pelas_palavras_chave() -> None:
    # A rede de palavras-chave encaminha "fragrância" ao especialista, que recebe
    # a mesma orientação pelo prompt (`MEMORIA_USUARIO_NOTA`).
    resultado = _rotear("tenho alergia a fragrância", "Anotado! Vou lembrar disso.")
    assert resultado["rota"] == "ingrediente"


def test_pergunta_com_alergia_continua_indo_ao_especialista() -> None:
    resultado = _rotear(
        "tenho alergia a fragrância, esse hidratante serve pra mim?",
        "ROUTE=produto\nPERGUNTA_ORIGINAL=tenho alergia a fragrância, esse hidratante serve pra mim?",
    )
    assert resultado["rota"] == "produto"
    assert "resposta_final" not in resultado


def test_informacao_pessoal_sem_alergia_mantem_a_resposta_do_llm() -> None:
    resultado = _rotear("tenho pele oleosa", "Que bom saber! Quer ajuda com algum produto?")
    assert resultado["resposta_final"] == "Que bom saber! Quer ajuda com algum produto?"


@pytest.mark.parametrize(
    "prompt", [ESP_PRODUTO_PROMPT_COMPLETO, ESP_INGREDIENTE_PROMPT_COMPLETO, ROTINA_PROMPT_COMPLETO, FAQ_PROMPT_COMPLETO]
)
def test_especialistas_sabem_que_a_memoria_nao_guarda_alergia(prompt: str) -> None:
    assert "A memória NÃO guarda alergias" in prompt
    assert "atualizar a lista\nde alergias no app" in prompt
