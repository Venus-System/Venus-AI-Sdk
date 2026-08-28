"""Nó Roteador: classifica a intenção e decide o especialista."""

from __future__ import annotations

import re
from typing import Literal

from venus_sdk.llm.models import llm_rapido
from venus_sdk.prompts.router import ROUTER_PROMPT_COMPLETO
from venus_sdk.state import EstadoVenus

# ROUTE= e PERGUNTA_ORIGINAL= são emitidos pelo LLM só quando o caso é de
# especialista; small talk/fora de escopo respondem em texto livre (ver
# `prompts/router.py`), daí a ausência de ROUTE= ser o sinal de resposta direta.
_ROUTE_RE = re.compile(r"ROUTE=(\w+)", re.IGNORECASE)
_PERGUNTA_RE = re.compile(r"PERGUNTA_ORIGINAL=(.*)", re.IGNORECASE | re.DOTALL)

_ROTAS_VALIDAS: frozenset[str] = frozenset({"produto", "ingrediente", "rotina", "faq"})

DecisaoRoteador = Literal["produto", "ingrediente", "rotina", "faq", "direto"]


def no_roteador(estado: EstadoVenus) -> EstadoVenus:
    """Chama o LLM roteador com `ROUTER_PROMPT_COMPLETO` e extrai o
    protocolo `ROUTE=.../PERGUNTA_ORIGINAL=...` (ou responde diretamente em
    caso de small talk/fora de escopo).
    """
    mensagem = estado.get("mensagem_anonimizada") or estado.get("mensagem_usuario", "")
    historico = estado.get("historico") or []

    mensagens = [("system", ROUTER_PROMPT_COMPLETO), *historico, ("human", mensagem)]
    resposta = llm_rapido.invoke(mensagens)
    texto = (resposta.content or "").strip()

    match_rota = _ROUTE_RE.search(texto)
    rota = match_rota.group(1).strip().lower() if match_rota else None

    if rota not in _ROTAS_VALIDAS:
        # Small talk ou fora de escopo: o próprio roteador já formulou a
        # resposta final ao usuário — segue direto para o guardrail de saída.
        return {"rota": None, "resposta_final": texto}

    match_pergunta = _PERGUNTA_RE.search(texto)
    pergunta_original = (
        match_pergunta.group(1).strip() if match_pergunta else estado.get("mensagem_usuario", "")
    )

    return {"rota": rota, "pergunta_original": pergunta_original}  # type: ignore[typeddict-item]


def decidir_especialista(estado: EstadoVenus) -> DecisaoRoteador:
    """Aresta condicional: lê `estado['rota']` e decide o próximo nó.

    Casa com as chaves usadas em `flows/venus_flow.py` — "direto" cobre o
    caso em que o roteador já respondeu (small talk/fora de escopo).
    """
    rota = estado.get("rota")
    if rota in _ROTAS_VALIDAS:
        return rota  # type: ignore[return-value]
    return "direto"
