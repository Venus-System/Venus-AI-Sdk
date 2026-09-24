"""Regras puras de guardrail — sem dependência do grafo/estado.

Consumidas pelos nós em `nodes/guardrails.py`. As checagens aqui são
determinísticas (regex) e propositalmente conservadoras: cobrem os casos
óbvios (mensagem vazia/gigante, spam/flood, tentativa de manipulação do
prompt — inclusive formas simples de evasão via leetspeak/acentuação —,
vazamento de dado sensível). Moderação de conteúdo mais sofisticada
(assédio, discurso de ódio etc.) fica a cargo do próprio comportamento do
LLM nos prompts de cada agente — não é reimplementada aqui.

Defesa em profundidade: além dessas regras determinísticas, os prompts em
`prompts/comum.py` (bloco `HIERARQUIA_INSTRUCOES`) reforçam a mesma recusa a
nível de LLM — cobre tentativas novas que ainda não têm regex aqui.
"""

from __future__ import annotations

import re
import unicodedata

TAMANHO_MAXIMO_MENSAGEM = 4000

MENSAGEM_ENTRADA_BLOQUEADA = (
    "Essa pergunta está fora do que eu consigo te ajudar! Mas podemos "
    "conversar sobre algum produto, ingrediente ou a sua rotina de "
    "skincare e haircare — por onde quer começar?"
)
MENSAGEM_SAIDA_BLOQUEADA = (
    "Não consegui te dar uma boa resposta pra isso. Pode reformular a "
    "pergunta ou falar comigo sobre algum produto, ingrediente ou rotina?"
)

# --- dados sensíveis (usados tanto para bloqueio de saída quanto anonimização) ---
_CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_RG_RE = re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}-[\dXx]\b")
_CEP_RE = re.compile(r"\b\d{5}-?\d{3}\b")
# Candidato a cartão: só a contagem de dígitos (13-19) não é filtro nenhum —
# batia em qualquer sequência longa de dígitos (ex.: código de barras EAN-13
# de produto, CEP+número concatenado). O regex aqui só encontra candidatos;
# quem decide se é cartão de verdade é `_eh_cartao_valido` (checksum de Luhn),
# chamado em cima de cada match antes de bloquear/mascarar.
_CARTAO_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_TELEFONE_RE = re.compile(r"\b(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}-?\d{4}\b")


def _eh_cartao_valido(candidato: str) -> bool:
    """Checksum de Luhn — filtra os falsos positivos do `_CARTAO_RE` (que só
    conta dígitos). Um código de barras/CEP/ID longo passa pela contagem mas
    quase nunca fecha o checksum de Luhn; um cartão real, sim."""
    digitos = [int(c) for c in candidato if c.isdigit()]
    soma = 0
    for i, digito in enumerate(reversed(digitos)):
        if i % 2 == 1:
            digito *= 2
            if digito > 9:
                digito -= 9
        soma += digito
    return soma % 10 == 0


# --- tentativa de manipulação do sistema (prompt injection / jailbreak) ---
# Aplicado direto no texto original (com acento) — os character classes
# ([çc], [ãa]...) já cobrem a variação com/sem acento sem precisar normalizar.
_INJECAO_RE = re.compile(
    # até 3 palavras de preenchimento entre "ignore"/"ignora" e "instruções"
    # (ex.: "ignore TODAS AS SUAS instruções anteriores") — sem isso, só
    # "ignore instruções"/"ignore as instruções" batiam, e uma frase natural
    # como essa passava direto pelo guardrail determinístico (visto de
    # verdade rodando o grafo completo em 2026-09-10; a defesa em prompt via
    # HIERARQUIA_INSTRUCOES pegou dessa vez, mas essa camada não deveria
    # depender só dela).
    r"ignor[ea]\s+(?:\w+\s+){0,3}instru[çc][õo]es|"
    r"esque[çc]a\s+(tudo|as\s+regras)|"
    r"revele\s+(seu\s+)?(system\s?)?prompt|"
    r"mostre\s+(o\s+)?(seu\s+)?prompt|"
    r"(qual|repita)\s+(é\s+|sã[oa]\s+)?(o\s+seu|suas?)\s+(prompt|instru[çc][õo]es)\s*(inicial|de\s+sistema)?|"
    r"modo\s+desenvolvedor|"
    r"modo\s+(sem\s+filtro|sem\s+censura|irrestrito|deus|god)|"
    r"sem\s+(filtro|censura|restri[çc][õo]es)\s+(nenhum[ao]|algum[ao])?|"
    r"dan\s+mode|"
    r"stan\s+mode|"
    r"jailbreak|"
    r"sudo\s+mode|"
    r"aja\s+como\s+se\s+voc[êe]\s+n[ãa]o\s+tivesse\s+regras|"
    r"finja\s+que\s+(voc[êe]\s+)?n[ãa]o\s+tem\s+regras|"
    r"saia\s+do\s+personagem|"
    r"fora\s+do\s+personagem|"
    r"out\s+of\s+character|"
    r"role\s*play\s+(como|as)\s+(uma?\s+)?IA\s+sem",
    re.IGNORECASE,
)

# Mesmo espírito de _INJECAO_RE, mas escrito sem acento — comparado contra
# `_normalizar_para_deteccao(texto)`, que remove acento e desfaz leetspeak
# básico (ign0re -> ignore). Cobre evasões simples que passariam pelo regex
# acima por não terem, literalmente, as palavras com acento certo.
_INJECAO_EVASAO_RE = re.compile(
    r"ignore\s+(?:\w+\s+){0,3}instrucoes|"
    r"esqueca\s+(tudo|as\s+regras)|"
    r"revele\s+(seu\s+)?prompt|"
    r"modo\s+desenvolvedor|"
    r"sem\s+filtro|"
    r"sem\s+censura|"
    r"dan\s+mode|"
    r"jailbreak|"
    r"hypothetically|"
    r"pretend\s+(you|to)\s+(are|be)|"
    r"unlock(ed)?\s+mode",
    re.IGNORECASE,
)

_LEETSPEAK = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s", "@": "a"})


def _normalizar_para_deteccao(texto: str) -> str:
    """Remove acento e desfaz leetspeak básico (`ign0re` -> `ignore`) só
    para rodar `_INJECAO_EVASAO_RE` contra uma forma mais difícil de
    escapar digitando — nunca usado para exibir, logar ou gravar."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sem_acento.lower().translate(_LEETSPEAK)


# --- spam / flood (mensagem inundando o mesmo caractere ou palavra) ---
_FLOOD_CARACTERE_RE = re.compile(r"(.)\1{19,}")  # mesmo caractere 20+ vezes seguidas
_FLOOD_PALAVRA_RE = re.compile(r"\b(\w+)\b(?:\s+\1\b){9,}", re.IGNORECASE)  # mesma palavra 10+ vezes

# --- emoji (a persona proíbe emoji em qualquer circunstância — ver
# PERSONA_SISTEMA em prompts/comum.py; como LLM não segue regra de estilo
# com 100% de confiabilidade, reforçamos removendo na saída) ---
_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"  # bandeiras (pares de letras regionais)
    "\U0001F300-\U0001FAFF"  # símbolos/pictogramas diversos, emoticons, transporte etc.
    "\U00002190-\U000021FF"  # setas (ex.: ↔️)
    "\U00002300-\U000023FF"  # símbolos técnicos diversos (ex.: ⌚ ⏰ ⏱)
    "\U000025A0-\U000027BF"  # formas geométricas, símbolos diversos e dingbats (☀-➿, ▶️, ✨💅-like ranges)
    "\U00002B00-\U00002BFF"  # setas/estrelas adicionais
    "\U0000FE0F"             # variation selector usado por emoji
    "\U0000200D"             # zero-width joiner (emoji composto, ex.: família)
    "\U000020E3"             # combining enclosing keycap (ex.: 1️⃣)
    "]+"
)
_ESPACO_ANTES_DE_PONTUACAO_RE = re.compile(r"\s+([.,!?;:])")
_ESPACOS_REPETIDOS_RE = re.compile(r" {2,}")


def _eh_tentativa_de_injecao(texto: str) -> bool:
    return bool(_INJECAO_RE.search(texto) or _INJECAO_EVASAO_RE.search(_normalizar_para_deteccao(texto)))


def _eh_flood(texto: str) -> bool:
    return bool(_FLOOD_CARACTERE_RE.search(texto) or _FLOOD_PALAVRA_RE.search(texto))


def _tem_cartao(texto: str) -> bool:
    return any(_eh_cartao_valido(m.group()) for m in _CARTAO_RE.finditer(texto))


def _tem_dado_sensivel_critico(texto: str) -> bool:
    """CPF/RG/cartão — dados que nunca devem sair na resposta. CEP fica de
    fora daqui (baixo risco, mas gera falso positivo com mais frequência;
    ver `anonimizar_entrada`, que mascara CEP na entrada mesmo assim)."""
    return bool(_CPF_RE.search(texto) or _RG_RE.search(texto) or _tem_cartao(texto))


def guardrail_entrada(mensagem: str) -> tuple[bool, str | None]:
    """Valida a mensagem do usuário antes de entrar no grafo.

    Retorna (bloqueado, motivo). `motivo` é None quando não bloqueado.
    """
    texto = (mensagem or "").strip()

    if not texto:
        return True, "mensagem vazia"

    if len(texto) > TAMANHO_MAXIMO_MENSAGEM:
        return True, "mensagem excede o tamanho máximo permitido"

    if _eh_flood(texto):
        return True, "mensagem parece spam/flood (caractere ou palavra repetida em excesso)"

    if _eh_tentativa_de_injecao(texto):
        return True, "tentativa de manipulação do sistema (prompt injection)"

    return False, None


def guardrail_saida(resposta: str) -> tuple[bool, str | None]:
    """Valida a resposta final antes de devolvê-la ao usuário."""
    texto = (resposta or "").strip()

    if not texto:
        return True, "resposta final vazia"

    if _tem_dado_sensivel_critico(texto):
        return True, "possível vazamento de dado sensível (CPF/RG/cartão)"

    if _eh_tentativa_de_injecao(texto):
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
    texto = _ESPACO_ANTES_DE_PONTUACAO_RE.sub(r"\1", texto)
    texto = _ESPACOS_REPETIDOS_RE.sub(" ", texto)
    return texto.strip()


def anonimizar_entrada(mensagem: str) -> str:
    """Remove/mascara dados sensíveis da mensagem do usuário antes de logar."""
    texto = mensagem or ""
    texto = _CPF_RE.sub("[CPF]", texto)
    texto = _RG_RE.sub("[RG]", texto)
    texto = _EMAIL_RE.sub("[EMAIL]", texto)
    texto = _CARTAO_RE.sub(lambda m: "[CARTAO]" if _eh_cartao_valido(m.group()) else m.group(), texto)
    texto = _CEP_RE.sub("[CEP]", texto)
    texto = _TELEFONE_RE.sub("[TELEFONE]", texto)
    return texto
