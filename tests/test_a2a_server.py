"""Testes do adaptador A2A (`venus_sdk.a2a_server`) — servidor A2A que expõe
o grafo compilado do Venus. Roda o app Starlette em processo (via
`httpx.ASGITransport`, sem precisar de porta/servidor real) e conversa com
ele usando o client oficial do `a2a-sdk`, igual um sistema externo faria.

Requer o extra opcional `a2a` (`pip install -e ".[a2a]"`) — sem ele, os
testes deste arquivo são pulados, não falham (mesmo espírito dos testes que
dependem do Mongo real, ver `tests/test_memoria.py`)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("a2a")

import httpx  # noqa: E402

from a2a.client import ClientConfig, create_client  # noqa: E402
from a2a.helpers import get_message_text, new_text_message  # noqa: E402
from a2a.types import Role, SendMessageRequest  # noqa: E402

from venus_sdk.a2a_server import montar_agent_card, montar_app_a2a  # noqa: E402
from venus_sdk.flows.venus_flow import compilar_grafo_venus  # noqa: E402
from venus_sdk.memory import criar_checkpointer_em_memoria  # noqa: E402

BASE_URL = "http://venus-a2a.teste"


def _resposta_llm(texto: str) -> SimpleNamespace:
    return SimpleNamespace(content=texto)


def _montar_app():
    grafo = compilar_grafo_venus(checkpointer=criar_checkpointer_em_memoria())
    return montar_app_a2a(grafo=grafo, base_url=BASE_URL)


async def _perguntar(app, texto: str, context_id: str) -> str:
    httpx_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL)
    client = await create_client(
        agent=montar_agent_card(BASE_URL),
        client_config=ClientConfig(httpx_client=httpx_client, streaming=False),
    )
    try:
        request = SendMessageRequest(
            message=new_text_message(texto, context_id=context_id, role=Role.ROLE_USER)
        )
        respostas = [evento async for evento in client.send_message(request)]
    finally:
        await client.close()
        await httpx_client.aclose()

    [evento] = respostas
    assert evento.message is not None
    return get_message_text(evento.message)


def test_agent_card_tem_as_skills_esperadas() -> None:
    card = montar_agent_card(BASE_URL)

    assert card.name == "Venus"
    ids_skills = {skill.id for skill in card.skills}
    assert {"produto", "ingrediente", "rotina", "faq"} <= set(ids_skills)


def test_task_ponta_a_ponta_bate_com_ainvoke_direto() -> None:
    """A resposta recebida via A2A tem que ser a mesma que o grafo devolve
    quando chamado direto via `.ainvoke()` — a camada A2A não deve alterar o
    conteúdo, só o transporte."""
    app = _montar_app()

    async def cenario() -> str:
        return await _perguntar(app, "oi", context_id="conversa-a2a-1")

    with patch("venus_sdk.nodes.roteador.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm("Oi! Como posso ajudar?")
        resposta = asyncio.run(cenario())

    assert resposta == "Oi! Como posso ajudar?"


def test_falha_do_llm_nao_derruba_a_resposta_a2a() -> None:
    """Mesma resiliência já existente em `nodes/roteador.py` (fallback
    determinístico quando o LLM roteador falha nas duas tentativas, ver
    `_RESPOSTA_DIRETA_FALLBACK`) — precisa continuar valendo através da
    camada A2A: uma falha de LLM tem que virar uma resposta de texto normal,
    nunca um crash/500 pro sistema externo que chamou o Venus."""
    app = _montar_app()

    async def cenario() -> str:
        return await _perguntar(app, "oi", context_id="conversa-a2a-erro")

    with patch("venus_sdk.nodes.roteador.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.side_effect = RuntimeError("provedor indisponível")
        resposta = asyncio.run(cenario())

    assert resposta  # não é vazio, não é exceção — a chamada A2A completou normalmente


def test_context_ids_diferentes_nao_vazam_historico() -> None:
    """Dois `context_id` diferentes viram `thread_id`s diferentes no
    checkpointer — não pode vazar histórico de uma conversa pra outra (mesmo
    espírito de `tests/test_memoria.py::test_historico_nao_vaza_entre_thread_ids_diferentes`)."""
    app = _montar_app()

    async def cenario() -> str:
        await _perguntar(app, "oi", context_id="sessao-x")
        return await _perguntar(app, "oi de novo", context_id="sessao-y")

    with patch("venus_sdk.nodes.roteador.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.side_effect = [
            _resposta_llm("Oi!"),
            _resposta_llm("Olá!"),
        ]
        resposta_y = asyncio.run(cenario())

    assert resposta_y == "Olá!"
