"""Client A2A do Venus — permite que um agente do grafo consulte um agente
EXTERNO via protocolo A2A (o contraponto de `a2a_server.py`, onde o Venus é
quem é consultado). Módulo opcional (extra `a2a`).

Agentes externos configuráveis por `A2A_AGENTES_EXTERNOS` (JSON
`{"nome": "http://host:porta"}`) ou passados a `montar_tool_a2a`."""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

from langchain_core.tools import BaseTool, tool

logger = logging.getLogger(__name__)


async def consultar_agente_externo(url: str, mensagem: str, *, httpx_client: Any | None = None,
                                   context_id: str | None = None) -> str:
    """Descobre o Agent Card em `url` e manda `mensagem` ao agente remoto,
    devolvendo o texto da resposta. Levanta exceção em falha de rede/protocolo
    (a tool `montar_tool_a2a` converte em resposta estruturada)."""
    from a2a.client import ClientConfig, create_client
    from a2a.helpers import get_message_text, new_text_message
    from a2a.types import Role, SendMessageRequest

    fechar = httpx_client is None
    if fechar:
        import httpx

        httpx_client = httpx.AsyncClient(timeout=60)
    client = await create_client(
        agent=url, client_config=ClientConfig(httpx_client=httpx_client, streaming=False)
    )
    try:
        request = SendMessageRequest(
            message=new_text_message(mensagem, context_id=context_id or str(uuid.uuid4()), role=Role.ROLE_USER)
        )
        textos = []
        async for evento in client.send_message(request):
            if evento.message is not None:
                textos.append(get_message_text(evento.message))
        return "\n".join(t for t in textos if t)
    finally:
        await client.close()
        if fechar:
            await httpx_client.aclose()


def carregar_agentes_externos(agentes: dict[str, str] | None = None) -> dict[str, str]:
    if agentes:
        return agentes
    bruto = os.getenv("A2A_AGENTES_EXTERNOS")
    return json.loads(bruto) if bruto else {}


def montar_tool_a2a(agentes: dict[str, str] | None = None, *, httpx_client: Any | None = None) -> list[BaseTool]:
    """Tool LangChain `consultar_agente_externo(agente, pergunta)`. Sem agentes
    configurados devolve `[]` (o especialista simplesmente não a tem)."""
    agentes = carregar_agentes_externos(agentes)
    if not agentes:
        return []
    lista = ", ".join(agentes)

    @tool("consultar_agente_externo")
    async def _consultar(agente: str, pergunta: str) -> dict:
        """Consulta um agente externo via A2A. `agente` deve ser um dos
        nomes disponíveis; `pergunta` é o texto a enviar. Devolve
        `{"agente", "resposta"}` — cite o agente como fonte."""
        if agente not in agentes:
            return {"erro": f"agente desconhecido; disponíveis: {lista}"}
        try:
            resposta = await consultar_agente_externo(agentes[agente], pergunta, httpx_client=httpx_client)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha ao consultar agente A2A %s: %s", agente, exc)
            return {"erro": "não consegui consultar o agente externo agora", "detalhe": type(exc).__name__}
        return {"agente": agente, "url": agentes[agente], "resposta": resposta or "(sem resposta)"}

    _consultar.description += f" Agentes disponíveis: {lista}."
    return [_consultar]
