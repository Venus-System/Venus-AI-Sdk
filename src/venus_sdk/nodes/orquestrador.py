"""Nó Orquestrador: transforma o JSON do especialista na resposta final."""

from __future__ import annotations

from venus_sdk.prompts.orquestrador import ORQUESTRADOR_PROMPT_COMPLETO
from venus_sdk.state import EstadoVenus


def no_orquestrador(estado: EstadoVenus) -> EstadoVenus:
    """TODO: chamar o LLM orquestrador com `ORQUESTRADOR_PROMPT_COMPLETO` e
    `resposta_especialista`, gravando o texto final em `resposta_final`.
    """
    raise NotImplementedError
