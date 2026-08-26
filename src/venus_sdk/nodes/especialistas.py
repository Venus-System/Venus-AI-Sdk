"""Nós dos agentes especialistas: produto, ingrediente, rotina e FAQ."""

from __future__ import annotations

from venus_sdk.prompts.faq import FAQ_PROMPT_COMPLETO
from venus_sdk.prompts.ingrediente import ESP_INGREDIENTE_PROMPT_COMPLETO
from venus_sdk.prompts.produto import ESP_PRODUTO_PROMPT_COMPLETO
from venus_sdk.prompts.rotina import ROTINA_PROMPT_COMPLETO
from venus_sdk.state import EstadoVenus


def no_agente_produto(estado: EstadoVenus) -> EstadoVenus:
    """TODO: rodar o agente MCP (ver `flows/agente_mcp.py`) com
    `ESP_PRODUTO_PROMPT_COMPLETO` e gravar o JSON em `resposta_especialista`.
    """
    raise NotImplementedError


def no_agente_ingrediente(estado: EstadoVenus) -> EstadoVenus:
    """TODO: idem, usando `ESP_INGREDIENTE_PROMPT_COMPLETO`."""
    raise NotImplementedError


def no_agente_rotina(estado: EstadoVenus) -> EstadoVenus:
    """TODO: idem, usando `ROTINA_PROMPT_COMPLETO`."""
    raise NotImplementedError


def no_agente_faq(estado: EstadoVenus) -> EstadoVenus:
    """TODO: usando `FAQ_PROMPT_COMPLETO`; grava a resposta final direto em
    `resposta_final` (o FAQ não passa pelo Agente Juiz).
    """
    raise NotImplementedError
