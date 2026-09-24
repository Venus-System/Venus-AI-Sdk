"""MCP (servidor stdio real em subprocesso + agente ReAct consumindo a tool)
e A2A (metadata de identidade + client consultando agente remoto)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from _fakes import LLMScript, chamada_tool
from langchain_core.messages import AIMessage

from venus_sdk.flows.agente_mcp import montar_agente_mcp
from venus_sdk.mcp.servidor import criar_servidor_mcp
from venus_sdk.mcp.tools import carregar_config_mcp, get_mcp_tools
from venus_sdk.rag import criar_indice_local

RAIZ = Path(__file__).resolve().parents[1]
FAQ = RAIZ / "data" / "faq"


def _cfg_stdio() -> dict:
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}  # só tools de FAQ sobem
    env["FAQ_DIR"] = str(FAQ)
    return {"venus": {"transport": "stdio", "command": sys.executable,
                      "args": ["-m", "venus_sdk.mcp.servidor"], "env": env, "cwd": str(RAIZ)}}


async def test_servidor_mcp_expoe_tools_e_responde_via_stdio() -> None:
    tools = {t.name: t for t in await get_mcp_tools(_cfg_stdio())}
    assert {"faq_retriever", "buscar_na_web"} <= set(tools)
    r = await tools["faq_retriever"].ainvoke({"pergunta": "como cadastrar alergias?"})
    assert "alergias_e_limites.md" in str(r)


async def test_agente_react_usa_tool_via_mcp() -> None:
    """O agente ReAct só tem tools vindas do servidor MCP — a resposta
    depende do retorno real da tool remota."""
    tools = await get_mcp_tools(_cfg_stdio(), apenas={"faq_retriever"})
    llm = LLMScript(script=[
        chamada_tool("faq_retriever", {"pergunta": "o venus vende meus dados?"}),
        lambda msgs: AIMessage(content="fonte: " + ("privacidade_e_dados.md" if "privacidade_e_dados.md" in str(msgs[-1].content) else "?")),
    ])
    r = await montar_agente_mcp(llm, tools=tools).ainvoke({"messages": [("human", "oi")]})
    assert r["messages"][-1].content == "fonte: privacidade_e_dados.md"


async def test_mcp_servidor_indisponivel_nao_derruba() -> None:
    cfg = {"x": {"transport": "stdio", "command": sys.executable, "args": ["-c", "raise SystemExit(1)"]}}
    assert await get_mcp_tools(cfg) == []


def test_montar_agente_sem_tools_falha_claro_e_sem_stub() -> None:
    with pytest.raises(ValueError):
        montar_agente_mcp(LLMScript(script=[AIMessage(content="x")]), tools=[])


def test_config_mcp_por_env(monkeypatch) -> None:
    monkeypatch.setenv("MCP_SERVERS", '{"a": {"transport": "stdio", "command": "x", "args": []}}')
    assert list(carregar_config_mcp()) == ["a"]


async def test_criar_servidor_registra_tools_com_schema() -> None:
    servidor = criar_servidor_mcp(indice=criar_indice_local(FAQ))
    nomes = {t.name: t for t in await servidor.list_tools()}
    assert set(nomes) == {"faq_retriever", "buscar_na_web"}
    assert "pergunta" in nomes["faq_retriever"].inputSchema["properties"]


# ---------------------------------------------------------------- A2A

pytest.importorskip("a2a")

from a2a.client import ClientConfig, create_client  # noqa: E402
from a2a.helpers import get_message_text, new_text_message  # noqa: E402
from a2a.types import Role, SendMessageRequest  # noqa: E402

from venus_sdk.a2a_client import consultar_agente_externo, montar_tool_a2a  # noqa: E402
from venus_sdk.a2a_server import montar_agent_card, montar_app_a2a  # noqa: E402

URL = "http://agente-externo.teste"


class _GrafoEspiao:
    def __init__(self) -> None:
        self.entradas: list[dict] = []

    async def ainvoke(self, entrada: dict, config: dict | None = None) -> dict:
        self.entradas.append({"entrada": entrada, "config": config})
        return {"resposta_final": f"eco: {entrada['mensagem_usuario']}"}


def _http(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL)


async def test_a2a_repassa_identidade_do_usuario_via_metadata() -> None:
    grafo = _GrafoEspiao()
    app = montar_app_a2a(grafo=grafo, base_url=URL)
    http = _http(app)
    client = await create_client(agent=montar_agent_card(URL),
                                 client_config=ClientConfig(httpx_client=http, streaming=False))
    req = SendMessageRequest(
        message=new_text_message("oi", context_id="ctx-1", role=Role.ROLE_USER),
        metadata={"usuario_id": "u-42", "usuario_id_postgres": 7},
    )
    [ev] = [e async for e in client.send_message(req)]
    await client.close(); await http.aclose()

    enviado = grafo.entradas[0]
    assert enviado["entrada"]["usuario_id"] == "u-42"
    assert enviado["entrada"]["usuario_id_postgres"] == 7
    assert enviado["config"]["configurable"]["thread_id"] == "ctx-1"
    assert get_message_text(ev.message) == "eco: oi"


async def test_client_a2a_consulta_agente_remoto_pelo_agent_card() -> None:
    app = montar_app_a2a(grafo=_GrafoEspiao(), base_url=URL)
    http = _http(app)
    resposta = await consultar_agente_externo(URL, "olá remoto", httpx_client=http)
    await http.aclose()
    assert resposta == "eco: olá remoto"


async def test_tool_a2a_consulta_e_trata_erros() -> None:
    app = montar_app_a2a(grafo=_GrafoEspiao(), base_url=URL)
    http = _http(app)
    [tool] = montar_tool_a2a({"externo": URL}, httpx_client=http)
    ok = await tool.ainvoke({"agente": "externo", "pergunta": "ping"})
    assert ok["resposta"] == "eco: ping" and ok["agente"] == "externo"
    assert "erro" in await tool.ainvoke({"agente": "outro", "pergunta": "x"})
    await http.aclose()

    [morto] = montar_tool_a2a({"morto": "http://127.0.0.1:1"})
    assert "erro" in await morto.ainvoke({"agente": "morto", "pergunta": "x"})


def test_tool_a2a_sem_agentes_configurados(monkeypatch) -> None:
    monkeypatch.delenv("A2A_AGENTES_EXTERNOS", raising=False)
    assert montar_tool_a2a() == []


async def test_agente_react_usa_tool_a2a() -> None:
    app = montar_app_a2a(grafo=_GrafoEspiao(), base_url=URL)
    http = _http(app)
    tools = montar_tool_a2a({"externo": URL}, httpx_client=http)
    llm = LLMScript(script=[
        chamada_tool("consultar_agente_externo", {"agente": "externo", "pergunta": "ping"}),
        lambda msgs: AIMessage(content=str(msgs[-1].content)),
    ])
    r = await montar_agente_mcp(llm, tools=tools).ainvoke({"messages": [("human", "x")]})
    await http.aclose()
    assert "eco: ping" in r["messages"][-1].content
