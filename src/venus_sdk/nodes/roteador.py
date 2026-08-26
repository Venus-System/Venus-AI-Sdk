"""Nó Roteador: classifica a intenção e decide o especialista."""

from __future__ import annotations

from venus_sdk.prompts.router import ROUTER_PROMPT_COMPLETO
from venus_sdk.state import EstadoVenus, Rota


def no_roteador(estado: EstadoVenus) -> EstadoVenus:
    """Chama o LLM roteador com `ROUTER_PROMPT_COMPLETO` e extrai o
    protocolo `ROUTE=.../PERGUNTA_ORIGINAL=...` (ou responde diretamente em
    caso de small talk/fora de escopo).

    TODO: implementar a chamada ao LLM e o parsing do protocolo.
    """
    raise NotImplementedError


def decidir_especialista(estado: EstadoVenus) -> Rota:
    """Aresta condicional: lê `estado['rota']` e decide o próximo nó.

    TODO: implementar — deve casar com as chaves usadas em
    `flows/venus_flow.py` (produto/ingrediente/rotina/faq).
    """
    raise NotImplementedError
