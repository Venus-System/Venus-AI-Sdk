"""Testes do nó Roteador e da aresta condicional decidir_especialista."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from venus_sdk.nodes.roteador import decidir_especialista, no_roteador


def _resposta_llm(texto: str) -> SimpleNamespace:
    return SimpleNamespace(content=texto)


@pytest.mark.parametrize(
    "rota, esperado",
    [
        ("produto", "produto"),
        ("ingrediente", "ingrediente"),
        ("rotina", "rotina"),
        ("faq", "faq"),
        (None, "direto"),
        ("fora_escopo", "direto"),
    ],
)
def test_decidir_especialista(rota: str | None, esperado: str) -> None:
    assert decidir_especialista({"rota": rota}) == esperado  # type: ignore[arg-type]


def test_no_roteador_encaminha_para_especialista() -> None:
    texto_llm = "ROUTE=produto\nPERGUNTA_ORIGINAL=por que esse produto foi recomendado?"
    with patch("venus_sdk.nodes.roteador.get_llm_roteador") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm(texto_llm)
        resultado = no_roteador({"mensagem_usuario": "por que esse produto foi recomendado?"})

    assert resultado["rota"] == "produto"
    assert resultado["pergunta_original"] == "por que esse produto foi recomendado?"
    get_llm_mock.return_value.invoke.assert_called_once()


def test_no_roteador_responde_direto_em_small_talk() -> None:
    texto_llm = "Olá! Posso te ajudar com produtos, ingredientes ou rotina; por onde quer começar?"
    with patch("venus_sdk.nodes.roteador.get_llm_roteador") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm(texto_llm)
        resultado = no_roteador({"mensagem_usuario": "oi, tudo bem?"})

    assert resultado["rota"] is None
    assert resultado["resposta_final"] == texto_llm


def test_no_roteador_tenta_de_novo_quando_llm_devolve_vazio() -> None:
    """Falha pontual do LLM (conteúdo vazio) não deve virar a mensagem
    genérica de saída bloqueada para algo simples como uma saudação —
    o roteador tenta mais uma vez antes de cair no fallback fixo."""
    texto_llm = "Oi! Como posso ajudar hoje?"
    with patch("venus_sdk.nodes.roteador.get_llm_roteador") as get_llm_mock:
        get_llm_mock.return_value.invoke.side_effect = [
            _resposta_llm(""),
            _resposta_llm(texto_llm),
        ]
        resultado = no_roteador({"mensagem_usuario": "oi"})

    assert resultado["rota"] is None
    assert resultado["resposta_final"] == texto_llm
    assert get_llm_mock.return_value.invoke.call_count == 2


def test_no_roteador_usa_fallback_quando_llm_falha_duas_vezes() -> None:
    with patch("venus_sdk.nodes.roteador.get_llm_roteador") as get_llm_mock:
        get_llm_mock.return_value.invoke.side_effect = [
            _resposta_llm(""),
            _resposta_llm("   "),
        ]
        resultado = no_roteador({"mensagem_usuario": "oi"})

    assert resultado["rota"] is None
    assert resultado["resposta_final"]  # nunca vazio
    assert get_llm_mock.return_value.invoke.call_count == 2


class _ErroToolCallAlucinada(Exception):
    """Emula `groq.BadRequestError` (tem `.body` com o mesmo formato do
    corpo de erro real da Groq) sem depender do pacote `groq` no teste."""

    def __init__(self, failed_generation: str) -> None:
        super().__init__("Tool choice is none, but model called a tool")
        self.body = {
            "error": {
                "message": "Tool choice is none, but model called a tool",
                "code": "tool_use_failed",
                "failed_generation": failed_generation,
            }
        }


def test_no_roteador_recupera_de_tool_call_alucinada() -> None:
    """Groq rejeita com 400 quando o gpt-oss-20b alucina uma tool call
    nativa pro protocolo ROUTE=/PERGUNTA_ORIGINAL= — o roteador recupera a
    decisão do `failed_generation` embutido no erro em vez de só tentar de
    novo (e só numa chamada, sem gastar o retry)."""
    erro = _ErroToolCallAlucinada(
        '{"name": "router", "arguments": '
        '{"ROUTE": "ingrediente", "PERGUNTA_ORIGINAL": "ácido hialurônico é seguro?"}}'
    )
    with patch("venus_sdk.nodes.roteador.get_llm_roteador") as get_llm_mock:
        get_llm_mock.return_value.invoke.side_effect = erro
        resultado = no_roteador({"mensagem_usuario": "ácido hialurônico é seguro?"})

    assert resultado["rota"] == "ingrediente"
    assert resultado["pergunta_original"] == "ácido hialurônico é seguro?"
    get_llm_mock.return_value.invoke.assert_called_once()


def test_no_roteador_recupera_de_tool_call_alucinada_no_retry() -> None:
    """Mesma recuperação, mas quando a alucinação só acontece na 2ª
    tentativa (1ª falhou por outro motivo transitório)."""
    erro_generico = Exception("timeout")
    erro_alucinado = _ErroToolCallAlucinada(
        '{"name": "router", "arguments": '
        '{"ROUTE": "produto", "PERGUNTA_ORIGINAL": "esse produto é bom pra pele oleosa?"}}'
    )
    with patch("venus_sdk.nodes.roteador.get_llm_roteador") as get_llm_mock:
        get_llm_mock.return_value.invoke.side_effect = [erro_generico, erro_alucinado]
        resultado = no_roteador({"mensagem_usuario": "esse produto é bom pra pele oleosa?"})

    assert resultado["rota"] == "produto"
    assert resultado["pergunta_original"] == "esse produto é bom pra pele oleosa?"
    assert get_llm_mock.return_value.invoke.call_count == 2


def test_no_roteador_usa_fallback_quando_excecao_nao_e_recuperavel() -> None:
    """Uma exceção sem o formato do erro de tool call (ex.: rate limit,
    timeout) continua caindo no retry normal e, se persistir, no fallback —
    não deve levantar."""
    with patch("venus_sdk.nodes.roteador.get_llm_roteador") as get_llm_mock:
        get_llm_mock.return_value.invoke.side_effect = Exception("rate limit exceeded")
        resultado = no_roteador({"mensagem_usuario": "oi"})

    assert resultado["rota"] is None
    assert resultado["resposta_final"]  # fallback fixo, nunca vazio


def test_no_roteador_reparseia_rota_no_retry() -> None:
    """Se a 1ª chamada vier vazia mas o retry vier com um ROUTE= válido, o
    retry precisa ser roteado normalmente — não pode virar texto cru
    devolvido como resposta_final ao usuário."""
    texto_retry = "ROUTE=ingrediente\nPERGUNTA_ORIGINAL=ácido hialurônico é seguro?"
    with patch("venus_sdk.nodes.roteador.get_llm_roteador") as get_llm_mock:
        get_llm_mock.return_value.invoke.side_effect = [
            _resposta_llm(""),
            _resposta_llm(texto_retry),
        ]
        resultado = no_roteador({"mensagem_usuario": "ácido hialurônico é seguro?"})

    assert resultado["rota"] == "ingrediente"
    assert resultado["pergunta_original"] == "ácido hialurônico é seguro?"
    assert "resposta_final" not in resultado
    assert get_llm_mock.return_value.invoke.call_count == 2
