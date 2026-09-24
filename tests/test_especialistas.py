"""Testes dos nós especialistas (`nodes/especialistas.py`) — foco no
protocolo de entrada (`_montar_entrada`), na extração de evidências das
tools pro Agente Juiz (`_extrair_evidencias_tools`) e na resiliência a
falha total do LLM/tools (`_executar_especialista`) e nos nós de rotina/FAQ."""

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
    montar_no_agente_faq,
    montar_no_agente_rotina,
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


# --- montar_no_agente_faq / montar_no_agente_rotina ---


def test_no_agente_faq_sem_indice_levanta_so_no_uso() -> None:
    """Igual a produto/ingrediente sem `pool`: montar o nó com `indice=None`
    não levanta (o grafo monta normalmente); o `ValueError` só sai quando o
    nó é de fato invocado."""
    no = montar_no_agente_faq(None)  # não levanta

    with pytest.raises(ValueError, match="índice"):
        _rodar(no({"rota": "faq", "pergunta_original": "como funciona o score?"}))


def test_no_agente_faq_devolve_json_com_fontes_e_evidencias() -> None:
    class _Indice:
        def buscar(self, consulta: str, k: int = 3):
            return [{"trecho": "O score vai de 0 a 100", "fonte": "como_funciona_o_score.md", "score": 0.9}]

    resposta = {"dominio": "faq", "intencao": "consultar_faq", "resposta": "De 0 a 100.",
                "recomendacao": "", "fontes_usadas": ["como_funciona_o_score.md"]}
    evidencias = [{"tool": "faq_retriever", "resultado": "[...]"}]
    with (
        patch("venus_sdk.nodes.especialistas.get_llm_especialista", return_value=object()),
        patch("venus_sdk.nodes.especialistas.montar_agente_mcp", return_value="agente-fake") as montar,
        patch("venus_sdk.nodes.especialistas._resposta_agente", new_callable=AsyncMock,
              return_value=(json.dumps(resposta), evidencias)),
    ):
        no = montar_no_agente_faq(_Indice(), tools_extras=["tool-extra-mcp"])
        resultado = _rodar(no({"rota": "faq", "pergunta_original": "como funciona o score?"}))

    assert resultado["resposta_especialista"]["fontes_usadas"] == ["como_funciona_o_score.md"]
    assert resultado["evidencias_tools"] == evidencias
    tools = montar.call_args.kwargs["tools"]
    assert [getattr(t, "name", t) for t in tools] == ["faq_retriever", "buscar_na_web", "tool-extra-mcp"]


def test_no_agente_rotina_sem_pool_levanta_so_no_uso() -> None:
    no = montar_no_agente_rotina(None)  # montar não levanta

    with pytest.raises(ValueError, match="pool"):
        _rodar(no({"rota": "rotina", "pergunta_original": "monta uma rotina"}))


def test_no_agente_faq_usa_fallback_quando_llm_falha() -> None:
    class _Indice:
        def buscar(self, consulta: str, k: int = 3):
            return []

    with (
        patch("venus_sdk.nodes.especialistas.get_llm_especialista", return_value=object()),
        patch("venus_sdk.nodes.especialistas.montar_agente_mcp", return_value="agente-fake"),
        patch("venus_sdk.nodes.especialistas._resposta_agente", new_callable=AsyncMock,
              side_effect=RuntimeError("provedor indisponível")),
    ):
        resultado = _rodar(montar_no_agente_faq(_Indice())({"rota": "faq", "pergunta_original": "x"}))

    assert resultado["resposta_especialista"]["intencao"] == "erro_tecnico"
    assert resultado["resposta_especialista"]["fontes_usadas"] == []


# --- _extrair_json: JSON cercado por ```json (visto ao vivo com o Gemini) ---

from venus_sdk.nodes.especialistas import _extrair_json  # noqa: E402


@pytest.mark.parametrize("texto", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    '```\n{"a": 1}\n```',
    'Aqui está:\n{"a": 1}\nEspero ter ajudado.',
    '  ```JSON\n{\n  "a": 1\n}\n```  ',
])
def test_extrair_json_tolera_cercas_e_texto_em_volta(texto: str) -> None:
    assert _extrair_json(texto) == {"a": 1}


@pytest.mark.parametrize("texto", ["", "sem json aqui", None])
def test_extrair_json_sem_objeto_levanta(texto) -> None:
    with pytest.raises((ValueError, TypeError)):
        _extrair_json(texto)


def test_extrair_json_tolera_chaves_acentuadas_e_quebra_de_linha_em_string() -> None:
    from venus_sdk.nodes.especialistas import _extrair_json

    bruto = '```json\n{"domínio": "ingrediente", "intenção": "explicar", "resposta": "linha1\nlinha2", "fontes_usadas": []}\n```'
    d = _extrair_json(bruto)
    assert d["dominio"] == "ingrediente" and d["intencao"] == "explicar" and "linha2" in d["resposta"]


def test_rotina_sem_passos_na_resposta_recebe_os_passos_reais_da_tool() -> None:
    import json

    from venus_sdk.nodes.especialistas import _garantir_passos_da_rotina

    ev = [{"tool": "suggest_routine", "resultado": json.dumps(
        {"horario": "manha", "passos": [{"ordem": 1, "nome": "Gel X", "categoria": "Limpeza"},
                                        {"ordem": 2, "nome": "Creme Y", "categoria": "Hidratante"}]})}]
    r = _garantir_passos_da_rotina({"resposta": "Sua rotina está pronta!", "recomendacao": ""}, ev)
    assert "1) Gel X (Limpeza)" in r["resposta"] and "2) Creme Y" in r["resposta"]
    # se a resposta já cita os produtos, não duplica
    ok = _garantir_passos_da_rotina({"resposta": "Use Gel X e depois Creme Y.", "recomendacao": ""}, ev)
    assert "Passos (" not in ok["resposta"]
