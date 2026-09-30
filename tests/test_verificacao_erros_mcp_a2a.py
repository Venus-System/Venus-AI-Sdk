"""Testes de verificação de erro para MCP e A2A que ainda não tinham cobertura:

- `criar_servidor_mcp` sem pool nem índice (zero tools, sem crash).
- `get_mcp_tools` com config malformada (JSON inválido em `MCP_SERVERS`) —
  precisa continuar devolvendo `[]` em vez de propagar a exceção, mesmo
  espírito de `test_mcp_servidor_indisponivel_nao_derruba` já existente,
  só que a falha agora é na hora de MONTAR o client, não de usá-lo.
- `VenusAgentExecutor.cancel()` levanta `UnsupportedOperationError` (o Venus
  não suporta cancelamento — comportamento documentado no código, nunca
  exercitado por um teste).
- `VenusAgentExecutor.execute()` quando o `grafo.ainvoke()` em si levanta uma
  exceção que NÃO é tratada internamente por nenhum nó (ex.: falha do
  checkpointer/store) — a resposta A2A tem que virar a mensagem genérica de
  erro, nunca um 500 cru. Os testes existentes de "falha do LLM" cobrem a
  resiliência DENTRO dos nós (roteador etc.), não o `except Exception` do
  próprio `VenusAgentExecutor.execute`.
- metadata A2A com `usuario_id_postgres` que não dá pra converter pra int —
  tem que ser ignorado (logado) e nunca derrubar a chamada.
"""

from __future__ import annotations

import pytest

from venus_sdk.mcp.servidor import criar_servidor_mcp
from venus_sdk.mcp.tools import get_mcp_tools


async def test_criar_servidor_mcp_sem_pool_nem_indice_nao_registra_tools() -> None:
    """Sem pool e sem índice, nenhuma tool é montada — mas o servidor sobe
    normalmente (usado hoje só pra healthcheck/inspeção, ver `mcp/servidor.py`)."""
    servidor = criar_servidor_mcp()
    assert await servidor.list_tools() == []


async def test_get_mcp_tools_config_json_invalido_nao_derruba(monkeypatch) -> None:
    """`MCP_SERVERS` malformado quebra dentro de `carregar_config_mcp` (chamada
    por `get_mcp_client`, dentro do `try` de `get_mcp_tools`) — tem que virar
    `[]`, igual um servidor fora do ar, não uma exceção crua pro chamador."""
    monkeypatch.setenv("MCP_SERVERS", "{isso nao é json valido")
    assert await get_mcp_tools() == []


async def test_get_mcp_tools_servidor_configurado_como_tipo_errado_nao_derruba() -> None:
    """Uma config MCP com um transporte/formato que o client rejeita na hora
    de montar (não só na hora de conectar) também não pode propagar."""
    cfg = {"x": {"transport": "isso-nao-existe"}}
    assert await get_mcp_tools(cfg) == []


# ---------------------------------------------------------------- A2A

pytest.importorskip("a2a")

import httpx  # noqa: E402
from a2a.client import ClientConfig, create_client  # noqa: E402
from a2a.helpers import get_message_text, new_text_message  # noqa: E402
from a2a.types import Role, SendMessageRequest  # noqa: E402
from a2a.utils.errors import UnsupportedOperationError  # noqa: E402

from venus_sdk.a2a_server import VenusAgentExecutor, montar_agent_card, montar_app_a2a  # noqa: E402

URL = "http://agente-erro.teste"


class _GrafoQueQuebra:
    """Simula uma falha que NENHUM nó trata internamente — ex.: o próprio
    `.ainvoke()` do `StateGraph` levantando (checkpointer/store fora do ar,
    bug de framework etc.), não uma falha de LLM já absorvida por um nó."""

    async def ainvoke(self, entrada: dict, config: dict | None = None) -> dict:
        raise RuntimeError("checkpointer indisponível")


class _GrafoEspiaoMetadata:
    def __init__(self) -> None:
        self.entradas: list[dict] = []

    async def ainvoke(self, entrada: dict, config: dict | None = None) -> dict:
        self.entradas.append(entrada)
        return {"resposta_final": "ok"}


def _http(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL)


async def test_a2a_executor_cancel_nao_e_suportado() -> None:
    executor = VenusAgentExecutor(grafo=_GrafoEspiaoMetadata())
    with pytest.raises(UnsupportedOperationError):
        await executor.cancel(context=None, event_queue=None)  # type: ignore[arg-type]


async def test_a2a_falha_do_proprio_ainvoke_vira_resposta_generica_sem_derrubar() -> None:
    """`grafo.ainvoke` levanta direto (não é uma falha de LLM já tratada por
    um nó) — `VenusAgentExecutor.execute` precisa capturar e devolver a
    mensagem genérica, nunca deixar a exceção subir pro transporte A2A."""
    app = montar_app_a2a(grafo=_GrafoQueQuebra(), base_url=URL)
    http = _http(app)
    client = await create_client(agent=montar_agent_card(URL),
                                 client_config=ClientConfig(httpx_client=http, streaming=False))
    req = SendMessageRequest(message=new_text_message("oi", context_id="ctx-falha", role=Role.ROLE_USER))
    [ev] = [e async for e in client.send_message(req)]
    await client.close(); await http.aclose()

    texto = get_message_text(ev.message)
    assert texto and "processar sua mensagem" in texto.lower()


async def test_a2a_usuario_id_postgres_invalido_e_ignorado_sem_derrubar() -> None:
    """`usuario_id_postgres` que não converte pra `int` é descartado (só
    logado) — a chamada tem que completar normalmente, sem esse campo."""
    grafo = _GrafoEspiaoMetadata()
    app = montar_app_a2a(grafo=grafo, base_url=URL)
    http = _http(app)
    client = await create_client(agent=montar_agent_card(URL),
                                 client_config=ClientConfig(httpx_client=http, streaming=False))
    req = SendMessageRequest(
        message=new_text_message("oi", context_id="ctx-meta-invalida", role=Role.ROLE_USER),
        metadata={"usuario_id_postgres": "não-é-um-número"},
    )
    [ev] = [e async for e in client.send_message(req)]
    await client.close(); await http.aclose()

    assert get_message_text(ev.message) == "ok"
    assert "usuario_id_postgres" not in grafo.entradas[0]


async def test_a2a_sem_metadata_nenhuma_nao_derruba() -> None:
    """Requisição sem `metadata` nenhum (nem no request, nem na mensagem) —
    caso mais comum de cliente A2A simples — precisa continuar funcionando."""
    grafo = _GrafoEspiaoMetadata()
    app = montar_app_a2a(grafo=grafo, base_url=URL)
    http = _http(app)
    client = await create_client(agent=montar_agent_card(URL),
                                 client_config=ClientConfig(httpx_client=http, streaming=False))
    req = SendMessageRequest(message=new_text_message("oi", context_id="ctx-sem-meta", role=Role.ROLE_USER))
    [ev] = [e async for e in client.send_message(req)]
    await client.close(); await http.aclose()

    assert get_message_text(ev.message) == "ok"
    assert "usuario_id" not in grafo.entradas[0] and "usuario_id_postgres" not in grafo.entradas[0]
