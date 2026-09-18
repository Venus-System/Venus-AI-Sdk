"""Testes dos nós especialistas (`nodes/especialistas.py`) — foco no
protocolo de entrada (`_montar_entrada`), na extração de evidências das
tools pro Agente Juiz (`_extrair_evidencias_tools`) e na resiliência a
falha total do LLM/tools (`_executar_especialista`, `no_agente_faq`)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from venus_sdk.nodes.especialistas import (
    _executar_especialista,
    _extrair_evidencias_tools,
    _montar_entrada,
    no_agente_faq,
    no_agente_rotina,
)


def _rodar(coro):
    return asyncio.run(coro)


# --- _montar_entrada: protocolo USER_ID_POSTGRES= ---


def test_montar_entrada_inclui_user_id_postgres_quando_presente() -> None:
    entrada = _montar_entrada(
        {"rota": "ingrediente", "pergunta_original": "pergunta", "usuario_id_postgres": 42}
    )

    assert "USER_ID_POSTGRES=42" in entrada


@pytest.mark.parametrize("estado_extra", [{}, {"usuario_id_postgres": None}])
def test_montar_entrada_omite_user_id_postgres_quando_ausente(estado_extra: dict) -> None:
    estado = {"rota": "ingrediente", "pergunta_original": "pergunta", **estado_extra}

    entrada = _montar_entrada(estado)

    assert "USER_ID_POSTGRES=" not in entrada


# --- _extrair_evidencias_tools ---


def test_extrair_evidencias_tools_pega_so_as_tool_messages() -> None:
    mensagens = [
        AIMessage(content=""),
        ToolMessage(name="search_ingredient", content="[]", tool_call_id="1"),
        ToolMessage(name="get_user_allergies", content='[{"allergy_name": "fragrância"}]', tool_call_id="2"),
        AIMessage(content='{"dominio": "ingrediente"}'),
    ]

    evidencias = _extrair_evidencias_tools(mensagens)

    assert evidencias == [
        {"tool": "search_ingredient", "resultado": "[]"},
        {"tool": "get_user_allergies", "resultado": '[{"allergy_name": "fragrância"}]'},
    ]


def test_extrair_evidencias_tools_vazio_sem_tool_messages() -> None:
    assert _extrair_evidencias_tools([AIMessage(content="oi")]) == []


# --- _executar_especialista: caminho feliz e resiliência ---


class _AgenteFalso:
    """Substitui o agente ReAct — `.ainvoke()` devolve mensagens fixas."""

    def __init__(self, mensagens: list[Any] | None = None, excecao: Exception | None = None) -> None:
        self._mensagens = mensagens
        self._excecao = excecao

    async def ainvoke(self, _entrada: dict) -> dict:
        if self._excecao is not None:
            raise self._excecao
        return {"messages": self._mensagens}


def test_executar_especialista_grava_resposta_e_evidencias() -> None:
    resposta_json = {"dominio": "ingrediente", "resposta": "ok", "fontes_usadas": ["search_ingredient"]}
    agente = _AgenteFalso(
        mensagens=[
            ToolMessage(name="search_ingredient", content="[]", tool_call_id="1"),
            AIMessage(content=json.dumps(resposta_json, ensure_ascii=False)),
        ]
    )

    resultado = _rodar(_executar_especialista({}, "ingrediente", agente))

    assert resultado["resposta_especialista"] == resposta_json
    assert resultado["evidencias_tools"] == [{"tool": "search_ingredient", "resultado": "[]"}]


def test_executar_especialista_json_invalido_cai_no_erro_formato_mas_mantem_evidencias() -> None:
    agente = _AgenteFalso(
        mensagens=[
            ToolMessage(name="search_ingredient", content="[]", tool_call_id="1"),
            AIMessage(content="isso não é JSON"),
        ]
    )

    resultado = _rodar(_executar_especialista({}, "ingrediente", agente))

    assert resultado["resposta_especialista"]["intencao"] == "erro_formato"
    assert resultado["evidencias_tools"] == [{"tool": "search_ingredient", "resultado": "[]"}]


def test_executar_especialista_excecao_no_llm_cai_no_erro_tecnico_sem_derrubar_o_grafo() -> None:
    """Gemini E o fallback Groq indisponíveis (ou qualquer outra falha
    dentro do agente ReAct) não devem subir crus até o `.ainvoke()` do
    grafo principal — viram um JSON reprovável normalmente pelo Agente Juiz
    (achado de um teste de conversa real em 2026-09-10)."""
    agente = _AgenteFalso(excecao=RuntimeError("provedor indisponível"))

    resultado = _rodar(_executar_especialista({}, "ingrediente", agente))

    assert resultado["resposta_especialista"]["intencao"] == "erro_tecnico"
    assert resultado["resposta_especialista"]["fontes_usadas"] == []
    assert resultado["evidencias_tools"] is None


# --- no_agente_faq/no_agente_rotina: stub (NotImplementedError) x falha de LLM ---


def test_no_agente_faq_degrada_graciosamente_quando_stub() -> None:
    """`NotImplementedError` na MONTAGEM do agente (client MCP ainda stub,
    `mcp/tools.py`) precisa virar uma resposta educada, não derrubar a
    conversa — o roteador (Groq) de vez em quando alucina uma rota errada
    (ver `nodes/roteador.py::_recuperar_de_tool_call_alucinada`) e manda
    small talk inofensivo pra cá; deixar isso crashar é pior que avisar que
    a funcionalidade ainda não existe (ver `no_agente_faq`)."""
    with patch("venus_sdk.nodes.especialistas._agente", side_effect=NotImplementedError("TODO: mcp")):
        resultado = _rodar(no_agente_faq({"mensagem_usuario": "qual a política de privacidade?"}))

    assert resultado["resposta_final"]  # nunca vazio, nunca levanta


def test_no_agente_rotina_degrada_graciosamente_quando_stub() -> None:
    with patch("venus_sdk.nodes.especialistas._agente", side_effect=NotImplementedError("TODO: mcp")):
        resultado = _rodar(no_agente_rotina({"mensagem_usuario": "monta uma rotina pra mim"}))

    assert resultado["resposta_especialista"]["intencao"] == "indisponivel"
    assert resultado["resposta_especialista"]["resposta"]
    assert resultado["evidencias_tools"] is None


def test_no_agente_faq_usa_fallback_quando_llm_falha_apos_agente_montado() -> None:
    with (
        patch("venus_sdk.nodes.especialistas._agente", return_value="agente-fake"),
        patch(
            "venus_sdk.nodes.especialistas._resposta_agente",
            new_callable=AsyncMock,
            side_effect=RuntimeError("provedor indisponível"),
        ),
    ):
        resultado = _rodar(no_agente_faq({"mensagem_usuario": "qual a política de privacidade?"}))

    assert resultado["resposta_final"]  # nunca vazio
