"""Testes do nó Orquestrador."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from venus_sdk.nodes.orquestrador import no_orquestrador


def _resposta_llm(texto: str) -> SimpleNamespace:
    return SimpleNamespace(content=texto)


def test_no_orquestrador_devolve_resposta_do_llm() -> None:
    texto_llm = "- Esse produto foi recomendado por causa do seu perfil.\n- *Recomendação*: use à noite."
    with patch("venus_sdk.nodes.orquestrador.get_llm_especialista") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm(texto_llm)
        resultado = no_orquestrador({"resposta_especialista": {"resposta": "ok"}})

    assert resultado["resposta_final"] == texto_llm
    get_llm_mock.return_value.invoke.assert_called_once()


def test_no_orquestrador_tenta_de_novo_quando_llm_devolve_vazio() -> None:
    texto_llm = "- Aqui está sua resposta.\n- *Recomendação*: siga o passo a passo."
    with patch("venus_sdk.nodes.orquestrador.get_llm_especialista") as get_llm_mock:
        get_llm_mock.return_value.invoke.side_effect = [
            _resposta_llm(""),
            _resposta_llm(texto_llm),
        ]
        resultado = no_orquestrador({"resposta_especialista": {"resposta": "ok"}})

    assert resultado["resposta_final"] == texto_llm
    assert get_llm_mock.return_value.invoke.call_count == 2


def test_no_orquestrador_usa_fallback_quando_llm_falha_duas_vezes() -> None:
    with patch("venus_sdk.nodes.orquestrador.get_llm_especialista") as get_llm_mock:
        get_llm_mock.return_value.invoke.side_effect = [
            _resposta_llm(""),
            _resposta_llm("   "),
        ]
        resultado = no_orquestrador({"resposta_especialista": {"resposta": "ok"}})

    assert resultado["resposta_final"]  # nunca vazio
    assert get_llm_mock.return_value.invoke.call_count == 2


def test_no_orquestrador_usa_fallback_quando_llm_levanta_excecao() -> None:
    """Gemini E o fallback Groq indisponíveis (ex.: cota estourada nos dois)
    não devem derrubar o `.ainvoke()` do grafo principal — só o caso de
    conteúdo vazio era tratado antes; uma exceção de verdade subia crua
    (achado de um teste de conversa real em 2026-09-10)."""
    with patch("venus_sdk.nodes.orquestrador.get_llm_especialista") as get_llm_mock:
        get_llm_mock.return_value.invoke.side_effect = RuntimeError("provedor indisponível")
        resultado = no_orquestrador({"resposta_especialista": {"resposta": "ok"}})

    assert resultado["resposta_final"]  # nunca vazio, mesmo com as duas tentativas falhando
    assert get_llm_mock.return_value.invoke.call_count == 2
