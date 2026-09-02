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

# --- emoji (a persona proíbe emoji em qualquer circunstância — ver
# PERSONA_SISTEMA em prompts/comum.py; como LLM não segue regra de estilo
# com 100% de confiabilidade, reforçamos removendo na saída) ---
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # símbolos/pictogramas diversos, emoticons, transporte etc.
    "\U00002600-\U000027BF"  # símbolos diversos e dingbats (☀-➿, inclui ✨💅-like ranges)
    "\U0001F1E6-\U0001F1FF"  # bandeiras (pares de letras regionais)
    "\U00002B00-\U00002BFF"  # setas/estrelas adicionais
    "\U0000FE0F"             # variation selector usado por emoji
    "]+"
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


def remover_emojis(resposta: str) -> str:
    """Remove emojis de uma resposta antes de devolvê-la ao usuário.

    A persona da Venus proíbe emoji em qualquer circunstância (regra
    absoluta — ver PERSONA_SISTEMA). Prompt sozinho não garante 100% de
    aderência de um LLM a uma regra de estilo, então isso é reforçado aqui
    de forma determinística, na saída."""
    texto = _EMOJI_RE.sub("", resposta or "")
    # Emoji costuma vir cercado de espaço (ex.: "Oi! 👋 Tudo bem?" ou
    # "ter 💅. Time"); depois de removê-lo, limpa o espaço órfão antes de
    # pontuação e o espaço duplo que sobra.
    texto = re.sub(r"\s+([.,!?;:])", r"\1", texto)
    texto = re.sub(r" {2,}", " ", texto)
    return texto.strip()


def anonimizar_entrada(mensagem: str) -> str:
    """Remove/mascara dados sensíveis da mensagem do usuário antes de logar."""
    texto = mensagem or ""
    texto = _CPF_RE.sub("[CPF]", texto)
    texto = _EMAIL_RE.sub("[EMAIL]", texto)
    texto = _CARTAO_RE.sub("[CARTAO]", texto)
    texto = _TELEFONE_RE.sub("[TELEFONE]", texto)
    return texto
