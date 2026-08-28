"""Regras puras de guardrail — sem dependência do grafo/estado.

Consumidas pelos nós em `nodes/guardrails.py`. As checagens aqui são
determinísticas (regex) e propositalmente conservadoras: cobrem os casos
óbvios (mensagem vazia/gigante, tentativa de manipulação do prompt,
vazamento de dado sensível). Moderação de conteúdo mais sofisticada
(assédio, discurso de ódio etc.) fica a cargo do próprio comportamento do
LLM nos prompts de cada agente — não é reimplementada aqui.
"""

from __future__ import annotations

import re

TAMANHO_MAXIMO_MENSAGEM = 4000

MENSAGEM_ENTRADA_BLOQUEADA = (
    "Não posso continuar com esse pedido. Posso ajudar com dúvidas sobre "
    "produtos, ingredientes, rotina ou o funcionamento do Venus."
)
MENSAGEM_SAIDA_BLOQUEADA = (
    "Não posso compartilhar essa resposta. Você pode reformular sua "
    "pergunta sobre produtos, ingredientes ou rotina?"
)

# --- dados sensíveis (usados tanto para bloqueio de saída quanto anonimização) ---
_CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_CARTAO_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_TELEFONE_RE = re.compile(r"\b(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}-?\d{4}\b")

# --- tentativa de manipulação do sistema (prompt injection) ---
_INJECAO_RE = re.compile(
    r"ignor[ea]\s+(as\s+)?instru[çc][õo]es|"
    r"esque[çc]a\s+(tudo|as\s+regras)|"
    r"revele\s+(seu\s+)?(system\s?)?prompt|"
    r"mostre\s+(o\s+)?(seu\s+)?prompt|"
    r"modo\s+desenvolvedor|"
    r"dan\s+mode|"
    r"aja\s+como\s+se\s+voc[êe]\s+n[ãa]o\s+tivesse\s+regras",
    re.IGNORECASE,
)


def guardrail_entrada(mensagem: str) -> tuple[bool, str | None]:
    """Valida a mensagem do usuário antes de entrar no grafo.

    Retorna (bloqueado, motivo). `motivo` é None quando não bloqueado.
    """
    texto = (mensagem or "").strip()

    if not texto:
        return True, "mensagem vazia"

    if len(texto) > TAMANHO_MAXIMO_MENSAGEM:
        return True, "mensagem excede o tamanho máximo permitido"

    if _INJECAO_RE.search(texto):
        return True, "tentativa de manipulação do sistema (prompt injection)"

    return False, None


def guardrail_saida(resposta: str) -> tuple[bool, str | None]:
    """Valida a resposta final antes de devolvê-la ao usuário."""
    texto = (resposta or "").strip()

    if not texto:
        return True, "resposta final vazia"

    if _CPF_RE.search(texto) or _CARTAO_RE.search(texto):
        return True, "possível vazamento de dado sensível (CPF/cartão)"

    if _INJECAO_RE.search(texto):
        return True, "resposta reflete tentativa de manipulação do sistema"

    return False, None


def anonimizar_entrada(mensagem: str) -> str:
    """Remove/mascara dados sensíveis da mensagem do usuário antes de logar."""
    texto = mensagem or ""
    texto = _CPF_RE.sub("[CPF]", texto)
    texto = _EMAIL_RE.sub("[EMAIL]", texto)
    texto = _CARTAO_RE.sub("[CARTAO]", texto)
    texto = _TELEFONE_RE.sub("[TELEFONE]", texto)
    return texto
