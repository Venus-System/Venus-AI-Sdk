"""Adaptador A2A do Venus — expõe o grafo compilado como um agente A2A
(https://a2a-protocol.org/), pra sistemas externos poderem "chamar" o Venus
como agente remoto sem conhecer a implementação interna (o contraponto do
MCP: lá é o Venus quem consome uma tool externa; aqui é o Venus quem é
consumido).

Módulo OPCIONAL — depende de `a2a-sdk[http-server]` (extra `a2a`, ver
`pyproject.toml`), nunca importado pelo resto do SDK. Quem sobe o servidor
de verdade decide host/porta/checkpointer/store/pool e injeta o grafo já
compilado aqui (mesmo espírito de `compilar_grafo_venus`: o SDK nunca cria
essas dependências sozinho).
"""

from __future__ import annotations

import logging
from typing import Any

from a2a.helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Role
from a2a.utils.errors import UnsupportedOperationError
from starlette.applications import Starlette
from starlette.routing import Route

logger = logging.getLogger(__name__)

# Rotina e FAQ ficam de fora das skills: `nodes/especialistas.py::no_agente_rotina`
# e `::no_agente_faq` ainda passam pelo client MCP genérico (stub em
# `mcp/tools.py`) e levantam `NotImplementedError` ao serem chamados — ver
# `docs/architecture.md`. Anunciar uma skill que quebra hoje seria pior que
# não anunciá-la.
_SKILLS = [
    AgentSkill(
        id="produto",
        name="Dúvidas sobre produto",
        description=(
            "Explica por que um produto foi recomendado, compara com o "
            "perfil do usuário e investiga relatos de reação/uso."
        ),
        tags=["skincare", "haircare", "produto"],
        input_modes=["text/plain"],
        output_modes=["text/plain"],
        examples=["Por que esse produto foi recomendado pra mim?"],
    ),
    AgentSkill(
        id="ingrediente",
        name="Dúvidas sobre ingrediente",
        description=(
            "Explica o que um ingrediente é, sua função, segurança e "
            "restrições regulatórias."
        ),
        tags=["skincare", "haircare", "ingrediente"],
        input_modes=["text/plain"],
        output_modes=["text/plain"],
        examples=["Niacinamida faz mal pra pele oleosa?"],
    ),
]


def montar_agent_card(base_url: str) -> AgentCard:
    """Agent Card do Venus — o "cartão de apresentação" que sistemas
    externos usam pra descobrir o que o Venus sabe fazer antes de chamar
    (servido em `GET /.well-known/agent-card.json`, ver `montar_app_a2a`).
    """
    return AgentCard(
        name="Venus",
        description=(
            "Assistente de skincare/haircare: explica recomendações de "
            "produto e tira dúvidas sobre ingredientes, sempre com base em "
            "dado real (nunca inventa)."
        ),
        version="0.1.0",
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=base_url)],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        skills=_SKILLS,
    )


class VenusAgentExecutor(AgentExecutor):
    """Roda o grafo compilado do Venus por trás do protocolo A2A.

    Segue o padrão "message-only" do A2A (uma única `Message` por request,
    sem `Task`/`TaskUpdater`) — o grafo do Venus responde de forma síncrona
    a cada turno, não há nada "long-running" pra acompanhar em etapas.
    """

    def __init__(self, grafo: Any) -> None:
        self._grafo = grafo

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        texto = context.get_user_input()
        # `context_id` do A2A vira o `thread_id` do checkpointer do Venus —
        # a mesma conversa (mesmo `context_id`) mantém o histórico entre
        # chamadas, exatamente como `thread_id` faz hoje (ver
        # `flows/venus_flow.py`/`memory/checkpointer.py`).
        config = {"configurable": {"thread_id": context.context_id}}

        try:
            estado = await self._grafo.ainvoke({"mensagem_usuario": texto}, config=config)
            resposta = estado.get("resposta_final") or "Não consegui gerar uma resposta agora."
        except Exception:
            # Mesmo espírito de `nodes/especialistas.py::_executar_especialista`
            # e `nodes/juiz.py::no_agente_juiz` — uma falha de LLM/infra nunca
            # deve derrubar a resposta A2A; vira uma mensagem de erro tratada,
            # não um 500 cru pro sistema externo que chamou o Venus.
            logger.exception("Falha ao executar o grafo Venus via A2A")
            resposta = "Não consegui processar sua mensagem agora — tente novamente em instantes."

        await event_queue.enqueue_event(
            new_text_message(resposta, context_id=context.context_id, role=Role.ROLE_AGENT)
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # O grafo do Venus responde de forma síncrona (sem Task de longa
        # duração pra cancelar) — não há o que cancelar no meio do caminho.
        raise UnsupportedOperationError("O Venus não suporta cancelamento de requisições em andamento.")


def montar_app_a2a(*, grafo: Any, base_url: str, rpc_path: str = "/") -> Starlette:
    """Monta o app Starlette do servidor A2A do Venus.

    `grafo` é o grafo já compilado (`compilar_grafo_venus(...)`) — quem sobe
    o servidor de verdade decide checkpointer/store/pool e injeta aqui.
    `base_url` é a URL pública onde este servidor vai ficar acessível (vai
    pro Agent Card, pra quem descobrir o Venus saber onde chamar).
    """
    agent_card = montar_agent_card(base_url)
    request_handler = DefaultRequestHandler(
        agent_executor=VenusAgentExecutor(grafo),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    rotas: list[Route] = []
    rotas.extend(create_agent_card_routes(agent_card))
    rotas.extend(create_jsonrpc_routes(request_handler, rpc_path))
    return Starlette(routes=rotas)
