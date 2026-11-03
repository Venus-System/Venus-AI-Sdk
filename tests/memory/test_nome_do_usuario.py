"""Nome da pessoa: primeiro nome do cadastro (`venus.users.name`), buscado uma
vez por conversa (`nodes/memoria.py::montar_no_carregar_nome`), e só ele vai
para o roteador — o resto da memória de longo prazo fica com os especialistas."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from _fakes import ConexaoFalsa, LLMScript, PoolFalso
from langchain_core.messages import AIMessage, HumanMessage

from venus_sdk.nodes import roteador
from venus_sdk.nodes.memoria import montar_no_carregar_nome, primeiro_nome
from venus_sdk.nodes.roteador import decidir_especialista, no_roteador


@pytest.mark.parametrize("nome, esperado", [
    ("Ana Paula Souza", "Ana"),
    ("  Bruno  ", "Bruno"),
    ("Ana (teste)", "Ana"),
    ("", ""),
    (None, ""),
])
def test_primeiro_nome(nome, esperado) -> None:
    assert primeiro_nome(nome) == esperado


def _carregar(estado: dict, conexao: ConexaoFalsa) -> dict:
    return asyncio.run(montar_no_carregar_nome(PoolFalso(conexao))(estado))


def test_busca_o_primeiro_nome_do_cadastro() -> None:
    conexao = ConexaoFalsa(fetchrow={"name": "Sophia Castro"})
    assert _carregar({"usuario_id_postgres": 7}, conexao) == {"nome_usuario": "Sophia"}
    assert conexao.chamadas[0][1] == (7,)


def test_conta_sem_linha_grava_vazio_para_nao_buscar_de_novo() -> None:
    assert _carregar({"usuario_id_postgres": 7}, ConexaoFalsa(fetchrow=None)) == {"nome_usuario": ""}


@pytest.mark.parametrize("estado", [
    {"usuario_id_postgres": 7, "nome_usuario": "Sophia"},
    {"usuario_id_postgres": 7, "nome_usuario": ""},
    {},
])
def test_nao_consulta_o_banco_sem_necessidade(estado) -> None:
    conexao = ConexaoFalsa(fetchrow={"name": "Outra"})
    assert _carregar(estado, conexao) == {}
    assert conexao.chamadas == []


def test_sem_pool_o_no_e_sincrono_e_nao_faz_nada() -> None:
    assert montar_no_carregar_nome(None)({"usuario_id_postgres": 7}) == {}


def test_falha_no_banco_segue_sem_nome() -> None:
    assert _carregar({"usuario_id_postgres": 7}, ConexaoFalsa(erro=RuntimeError("fora do ar"))) == {}


# --- roteador ---


def _entrada_do_roteador(estado: dict) -> str:
    return roteador._mensagens_para_o_roteador({"mensagem_usuario": "oi", **estado})[-1][1]


def test_roteador_recebe_so_o_nome_da_memoria() -> None:
    entrada = _entrada_do_roteador({
        "nome_usuario": "Sophia",
        "memorias_usuario": {"tipo_pele": "oleosa", "objetivo": "tratar acne", "alergias": ["parfum"]},
    })
    assert entrada == "NOME_USUARIO=Sophia\noi"


def test_nome_dito_no_chat_vale_mais_que_o_do_cadastro() -> None:
    entrada = _entrada_do_roteador({"nome_usuario": "Sophia", "memorias_usuario": {"nome": "Sophi"}})
    assert entrada.startswith("NOME_USUARIO=Sophi\n")


def test_sem_nome_o_roteador_recebe_so_a_mensagem() -> None:
    assert _entrada_do_roteador({"nome_usuario": "", "memorias_usuario": {"tipo_pele": "seca"}}) == "oi"


def test_pergunta_pelo_nome_do_cadastro_ja_carregado_e_respondida_direto() -> None:
    llm = LLMScript(script=[AIMessage(content="Sophia! Já te decorei??")])
    estado = {"mensagem_usuario": "qual é o meu nome?", "historico": [HumanMessage("qual é o meu nome?")],
              "usuario_id_postgres": 142, "nome_usuario": "Sophia"}
    with patch("venus_sdk.nodes.roteador.get_llm_rapido", return_value=llm):
        resultado = no_roteador(estado)
    assert decidir_especialista(resultado) == "direto"
    assert resultado["resposta_final"] == "Sophia! Já te decorei??"


def test_resposta_direta_nao_ecoa_a_linha_do_nome() -> None:
    llm = LLMScript(script=[AIMessage(content="NOME_USUARIO=Sophia\nOii, Sophia!! Tudo bem??")])
    with patch("venus_sdk.nodes.roteador.get_llm_rapido", return_value=llm):
        resultado = no_roteador({"mensagem_usuario": "oi", "historico": [], "nome_usuario": "Sophia"})
    assert resultado["resposta_final"] == "Oii, Sophia!! Tudo bem??"
