"""Nó Agente Juiz: valida a saída dos especialistas antes do orquestrador."""

from __future__ import annotations

import json
import re
from typing import Literal

from venus_sdk.llm.models import llm_rapido
from venus_sdk.prompts.juiz import JUIZ_PROMPT_COMPLETO
from venus_sdk.state import EstadoVenus

ResultadoJuiz = Literal["aprovado", "reprovado", "esgotado"]

# Nº máximo de vezes que o Agente Juiz pode mandar o especialista tentar de
# novo (via roteador) antes de seguir mesmo assim para o orquestrador.
MAX_TENTATIVAS_JUIZ = 2

_RESULTADO_RE = re.compile(r"RESULTADO=(\w+)", re.IGNORECASE)
_FEEDBACK_RE = re.compile(r"FEEDBACK=(.*)", re.IGNORECASE | re.DOTALL)


def no_agente_juiz(estado: EstadoVenus) -> EstadoVenus:
    """Avalia `resposta_especialista` e atualiza `aprovado_juiz`,
    `feedback_juiz` e `tentativas_juiz`."""
    entrada = (
        f"PERGUNTA_ORIGINAL={estado.get('pergunta_original', '')}\n"
        f"ESPECIALISTA_JSON={json.dumps(estado.get('resposta_especialista') or {}, ensure_ascii=False)}"
    )
    mensagens = [("system", JUIZ_PROMPT_COMPLETO), ("human", entrada)]
    resposta = llm_rapido.invoke(mensagens)
    texto = (resposta.content or "").strip()

    match_resultado = _RESULTADO_RE.search(texto)
    aprovado = bool(match_resultado) and match_resultado.group(1).strip().lower() == "aprovado"

    feedback = None
    if not aprovado:
        match_feedback = _FEEDBACK_RE.search(texto)
        feedback = match_feedback.group(1).strip() if match_feedback else texto

    return {
        "aprovado_juiz": aprovado,
        "feedback_juiz": feedback,
        "tentativas_juiz": estado.get("tentativas_juiz", 0) + 1,
    }


def decidir_pos_juiz(estado: EstadoVenus) -> ResultadoJuiz:
    """Aresta condicional pós-juiz.

    Devolve "reprovado" enquanto houver tentativas disponíveis (volta para o
    roteador/especialista), e "esgotado" quando as tentativas acabarem
    (segue para o orquestrador mesmo sem aprovação total).
    """
    if estado.get("aprovado_juiz"):
        return "aprovado"
    if estado.get("tentativas_juiz", 0) >= MAX_TENTATIVAS_JUIZ:
        return "esgotado"
    return "reprovado"
