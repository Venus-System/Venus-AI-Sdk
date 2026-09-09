"""Nó Roteador: classifica a intenção e decide o especialista."""

from __future__ import annotations

import json
import logging
import re
from typing import Literal

from venus_sdk.llm.models import get_llm_rapido
from venus_sdk.prompts.router import ROUTER_PROMPT_COMPLETO
from venus_sdk.state import EstadoVenus

logger = logging.getLogger(__name__)

# ROUTE= e PERGUNTA_ORIGINAL= são emitidos pelo LLM só quando o caso é de
# especialista; small talk/fora de escopo respondem em texto livre (ver
# `prompts/router.py`), daí a ausência de ROUTE= ser o sinal de resposta direta.
_ROUTE_RE = re.compile(r"ROUTE=(\w+)", re.IGNORECASE)
_PERGUNTA_RE = re.compile(r"PERGUNTA_ORIGINAL=(.*)", re.IGNORECASE | re.DOTALL)

_ROTAS_VALIDAS: frozenset[str] = frozenset({"produto", "ingrediente", "rotina", "faq"})

DecisaoRoteador = Literal["produto", "ingrediente", "rotina", "faq", "direto"]

# Fallback para quando o roteador não emite ROUTE= (small talk/fora de
# escopo) e, mesmo assim, o LLM devolve conteúdo vazio (falha pontual do
# modelo). Evita cair na mensagem genérica de saída bloqueada por algo tão
# simples quanto uma saudação.
_RESPOSTA_DIRETA_FALLBACK = (
    "Oii, tudo bem?? Posso te ajudar com produto, ingrediente ou rotina de "
    "skincare/haircare — quais dúvidas você tem hoje??"
)


def _recuperar_de_tool_call_alucinada(erro: Exception) -> str | None:
    """Recupera ROUTE=/PERGUNTA_ORIGINAL= de dentro do erro 400 da Groq
    quando o gpt-oss-20b alucina uma tool call nativa (ex.: um "tool"
    chamado "router" tentando emitir o protocolo como argumentos) mesmo sem
    nenhuma tool vinculada ao client — a Groq rejeita a chamada inteira com
    `400 "Tool choice is none, but model called a tool"` (`code ==
    "tool_use_failed"`) nesse caso.

    Em vez de só tentar de novo (a alucinação não é 100% determinística nem
    a `temperature=0.0` — retries às vezes se repetem, cada um consumindo
    mais uma chamada da cota), a Groq devolve o `failed_generation` no corpo
    do erro com a decisão que o modelo já tinha tomado; extrai ela dali e
    reconstrói o protocolo em texto puro, evitando desperdiçar a resposta.

    Devolve `None` (nunca levanta) se o erro não for esse caso específico ou
    se o corpo não tiver o formato esperado — quem chama cai no retry normal.
    """
    corpo = getattr(erro, "body", None)
    if not isinstance(corpo, dict):
        return None
    detalhe = corpo.get("error")
    if not isinstance(detalhe, dict) or detalhe.get("code") != "tool_use_failed":
        return None
    bruto = detalhe.get("failed_generation")
    if not bruto:
        return None
    try:
        argumentos = json.loads(bruto)["arguments"]
        rota = argumentos["ROUTE"]
        pergunta = argumentos["PERGUNTA_ORIGINAL"]
    except (TypeError, ValueError, KeyError):
        return None

    logger.warning(
        "LLM roteador alucinou uma tool call (ROUTE=%s); recuperando do corpo do "
        "erro em vez de tentar de novo",
        rota,
    )
    return f"ROUTE={rota}\nPERGUNTA_ORIGINAL={pergunta}"


def _invocar_roteador(mensagens: list) -> str:
    # Bug de content vazio: ver `get_llm_rapido`. Tenta mais uma vez antes de
    # desistir, em vez de deixar a exceção derrubar o grafo inteiro.
    try:
        resposta = get_llm_rapido().invoke(mensagens)
    except Exception as erro:
        recuperado = _recuperar_de_tool_call_alucinada(erro)
        if recuperado is not None:
            return recuperado
        logger.warning("Falha ao chamar o LLM roteador; tentando novamente uma vez", exc_info=True)
        try:
            resposta = get_llm_rapido().invoke(mensagens)
        except Exception as erro2:
            recuperado = _recuperar_de_tool_call_alucinada(erro2)
            if recuperado is not None:
                return recuperado
            logger.exception("Segunda tentativa do LLM roteador também falhou")
            return ""
    return (resposta.content or "").strip()


def no_roteador(estado: EstadoVenus) -> EstadoVenus:
    """Chama o LLM roteador com `ROUTER_PROMPT_COMPLETO` e extrai o
    protocolo `ROUTE=.../PERGUNTA_ORIGINAL=...` (ou responde diretamente em
    caso de small talk/fora de escopo).
    """
    mensagem = estado.get("mensagem_anonimizada") or estado.get("mensagem_usuario", "")
    memorias = estado.get("memorias_usuario")
    if memorias:
        mensagem = f"MEMORIA_USUARIO={json.dumps(memorias, ensure_ascii=False)}\n{mensagem}"

    # `historico` já inclui a mensagem deste turno — `no_guardrail_entrada`
    # grava `mensagem_anonimizada` nele (reducer `add_messages`, ver
    # `state.py`) antes do roteador rodar, no mesmo `invoke`. Descartamos a
    # última entrada aqui pra não duplicá-la no prompt do LLM (ela reaparece
    # logo abaixo, já com o prefixo MEMORIA_USUARIO= quando houver); isso
    # vale tanto na primeira passagem quanto num retry vindo do Agente Juiz
    # ("reprovado" -> volta pro roteador sem o guardrail rodar de novo), já
    # que `mensagem_anonimizada` não muda entre essas tentativas.
    historico = list(estado.get("historico") or [])
    if historico:
        historico = historico[:-1]
    mensagens = [("system", ROUTER_PROMPT_COMPLETO), *historico, ("human", mensagem)]

    texto = _invocar_roteador(mensagens)
    if not texto:
        # Falha pontual do LLM (conteúdo vazio); tenta mais uma vez antes de
        # decidir — o retry passa pelo mesmo parsing de ROUTE= abaixo, então
        # se ele vier com uma rota válida isso não vira texto cru pro usuário.
        texto = _invocar_roteador(mensagens)

    match_rota = _ROUTE_RE.search(texto)
    rota = match_rota.group(1).strip().lower() if match_rota else None

    if rota not in _ROTAS_VALIDAS:
        # Small talk ou fora de escopo: o próprio roteador já formulou a
        # resposta final ao usuário — segue direto para o guardrail de saída.
        return {"rota": None, "resposta_final": texto or _RESPOSTA_DIRETA_FALLBACK}

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
