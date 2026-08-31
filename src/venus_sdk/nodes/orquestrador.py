"""Nó Orquestrador: transforma o JSON do especialista na resposta final."""

from __future__ import annotations

import json

from venus_sdk.llm.models import get_llm_especialista
from venus_sdk.prompts.orquestrador import ORQUESTRADOR_PROMPT_COMPLETO
from venus_sdk.state import EstadoVenus

_NOTA_JUIZ_ESGOTADO = (
    "NOTA_DO_SISTEMA=O Agente Juiz não conseguiu validar totalmente esta "
    "resposta após as tentativas disponíveis. Comunique isso ao usuário com "
    "transparência, sem alarmismo."
)


def no_orquestrador(estado: EstadoVenus) -> EstadoVenus:
    """Chama o LLM orquestrador com `ORQUESTRADOR_PROMPT_COMPLETO` e
    `resposta_especialista`, gravando o texto final em `resposta_final`."""
    especialista_json = estado.get("resposta_especialista") or {}
    entrada = f"ESPECIALISTA_JSON={json.dumps(especialista_json, ensure_ascii=False)}"

    # Chegou aqui via "esgotado" (ver `nodes/juiz.py`) sem aprovação plena.
    if not estado.get("aprovado_juiz", True):
        entrada += "\n\n" + _NOTA_JUIZ_ESGOTADO

    mensagens = [("system", ORQUESTRADOR_PROMPT_COMPLETO), ("human", entrada)]
    resposta = get_llm_especialista().invoke(mensagens)

    return {"resposta_final": resposta.content}
