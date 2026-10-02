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
# CEP: com hífen, ou 8 dígitos logo depois da palavra "CEP" — 8 dígitos
# soltos são quase sempre outra coisa (código de produto, pedido...).
_CEP_RE = re.compile(r"\b(cep\W{0,3})?(\d{5}-\d{3}|\d{8})\b", re.IGNORECASE)
# Candidato a cartão: só a contagem de dígitos (13-19) não é filtro nenhum —
# batia em qualquer sequência longa de dígitos (ex.: código de barras EAN-13
# de produto, CEP+número concatenado). O regex aqui só encontra candidatos;
# quem decide se é cartão de verdade é `_eh_cartao_valido` (checksum de Luhn),
# chamado em cima de cada match antes de bloquear/mascarar.
_CARTAO_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_TELEFONE_RE = re.compile(r"\b(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}-?\d{4}\b")


def _eh_cpf(candidato: str) -> bool:
    """CPF formatado (com ponto ou hífen) conta sempre; 11 dígitos soltos só
    com os dígitos verificadores certos — senão é código de barras, telefone..."""
    if not candidato.isdigit():
        return True
    digitos = [int(c) for c in candidato]
    if len(set(digitos)) == 1:
        return False
    for tamanho in (9, 10):
        soma = sum(d * peso for d, peso in zip(digitos[:tamanho], range(tamanho + 1, 1, -1)))
        if (soma * 10 % 11) % 10 != digitos[tamanho]:
            return False
    return True


def _mascarar_cep(match: re.Match[str]) -> str:
    prefixo, numero = match.group(1), match.group(2)
    if prefixo or "-" in numero:
        return f"{prefixo or ''}[CEP]"
    return match.group()


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
    # até 3 palavras entre "ignore" e "instruções" ("ignore TODAS AS SUAS
    # instruções anteriores").
    r"ignor[ea]\s+(?:\w+\s+){0,3}instru[çc][õo]es|"
    r"esque[çc]a\s+(tudo|as\s+regras)|"
    r"revele\s+(seu\s+)?(system\s?)?prompt|"
    r"mostre\s+(o\s+)?(seu\s+)?prompt|"
    r"(qual|repita)\s+(é\s+|sã[oa]\s+)?(o\s+seu|suas?)\s+(prompt|instru[çc][õo]es)\s*(inicial|de\s+sistema)?|"
    r"modo\s+desenvolvedor|"
    r"modo\s+(sem\s+filtro|sem\s+censura|irrestrito|deus|god)|"
    # "sem restrições" sozinho é frase comum ("ingrediente sem restrições
    # regulatórias") e bloqueava respostas legítimas na saída — só conta
    # com um verbo de comando antes ou com "nenhuma/alguma" junto.
    # "sem filtro" sozinho é vocabulário de protetor solar ("sem filtro
    # químico"): só conta com um verbo de comando antes.
    r"sem\s+censura\b|"
    r"(respond|fal|aj|atu|oper|funcion|convers|modo)\w*\s+sem\s+filtros?\b|"
    r"sem\s+restri[çc][õo]es\s+(nenhum[ao]|algum[ao])\b|"
    r"sem\s+nenhuma\s+restri[çc][ãa]o|"
    r"(respond|fal|aj|atu|oper|funcion|convers)\w*\s+sem\s+restri[çc][õo]es|"
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
    r"sem\s+censura|"
    r"(respond|fal|aj|atu|oper|funcion|convers|modo)\w*\s+sem\s+filtros?\b|"
    r"dan\s+mode|"
    r"jailbreak|"
    r"hypothetically|"
    r"pretend\s+(you|to)\s+(are|be)|"
    r"unlock(ed)?\s+mode",
    re.IGNORECASE,
)

# Inglês e verbos/objetos em português, comparados contra o texto já
# normalizado (sem acento, minúsculo, sem leetspeak e com letras espaçadas
# juntadas). Os objetos são específicos ("suas regras", "regras anteriores",
# "diretrizes", "prompt"...) para não pegar pergunta comum como "quais as
# regras para usar retinol?" ou "posso ignorar o protetor em casa?".
_INJECAO_AMPLIADA_RE = re.compile(
    r"\b(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+|your\s+)*(previous|prior|above|earlier)?\s*"
    r"(instructions|rules|directives|prompts?)\b|"
    r"\bsystem\s*prompt\b|"
    r"\byou\s+are\s+now\b|"
    r"\bdeveloper\s+mode\b|"
    r"\b(desconsider\w*|ignor\w*|esquec\w*|descart\w*|abandon\w*)\s+(?:\w+\s+){0,3}?"
    r"(diretrizes|(suas|tuas)\s+regras|regras\s+(anteriores|do\s+sistema)|instrucoes|"
    r"o\s+que\s+te\s+(disseram|falaram|pediram|ensinaram)|(o\s+|seu\s+|teu\s+)?prompt)\b|"
    r"\bprompt\s+(de|do)\s+sistema\b|"
    r"\b(seu|teu)\s+prompt\b"
)
# "DAN" ("Do Anything Now") só em maiúsculas: "Dan" é nome de gente.
_DAN_RE = re.compile(r"\bDAN\b")
# Letras isoladas separadas por espaço/pontuação ("i g n o r e", "i.g.n.o.r.e")
# viram uma palavra só antes da checagem.
_LETRAS_ESPACADAS_RE = re.compile(r"\b(?:[a-z][\s.\-_*]+){2,}[a-z]\b")

_LEETSPEAK = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s", "@": "a"})


def _normalizar_para_deteccao(texto: str) -> str:
    """Remove acento e desfaz leetspeak básico (`ign0re` -> `ignore`) só
    para rodar `_INJECAO_EVASAO_RE` contra uma forma mais difícil de
    escapar digitando — nunca usado para exibir, logar ou gravar."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    normalizado = sem_acento.lower().translate(_LEETSPEAK)
    return _LETRAS_ESPACADAS_RE.sub(lambda m: re.sub(r"[\s.\-_*]", "", m.group()), normalizado)


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
    "\U00002300-\U000023FF"  # símbolos técnicos diversos (ex.: ⌚ ⏰ ⏱)
    "\U00002B00-\U00002BFF"  # setas/estrelas adicionais
    "\U0000FE0F"             # variation selector usado por emoji
    "\U0000200D"             # zero-width joiner (emoji composto, ex.: família)
    "\U000020E3"             # combining enclosing keycap (ex.: 1️⃣)
    "]+"
)
# Seta ou símbolo seguido do seletor de emoji (U+FE0F, ex.: "▶️", "↔️") é
# emoji; sem ele, é texto ("→", "✓").
_SIMBOLO_EM_FORMA_DE_EMOJI_RE = re.compile("[←-⇿■-➿]️")
# Faixa de símbolos diversos/dingbats (☀ ✨ ❤): emoji, exceto os símbolos
# tipográficos que aparecem em texto comum.
_SIMBOLOS_DIVERSOS_RE = re.compile("[\U000025A0-\U000027BF]")
_SIMBOLOS_DE_TEXTO = frozenset("■□▪▫▲△▶▷►▼▽◀◁◆◇○●◦★☆✓✔✗✘•")
_ESPACO_ANTES_DE_PONTUACAO_RE = re.compile(r"\s+([.,!?;:])")
# Só espaços no meio do texto: a indentação no início da linha (listas
# aninhadas) é preservada.
_ESPACOS_REPETIDOS_RE = re.compile(r"(?<=\S) {2,}")


def contem_tentativa_de_injecao(texto: str) -> bool:
    """True se o texto tenta manipular o sistema (prompt injection) — também
    usado em conteúdo que vem de fora (web, A2A, memória)."""
    return _eh_tentativa_de_injecao(texto)


def _eh_tentativa_de_injecao(texto: str) -> bool:
    normalizado = _normalizar_para_deteccao(texto)
    return bool(
        _INJECAO_RE.search(texto)
        or _DAN_RE.search(texto)
        or _INJECAO_EVASAO_RE.search(normalizado)
        or _INJECAO_AMPLIADA_RE.search(normalizado)
    )


def _eh_flood(texto: str) -> bool:
    return bool(_FLOOD_CARACTERE_RE.search(texto) or _FLOOD_PALAVRA_RE.search(texto))


def _tem_cartao(texto: str) -> bool:
    return any(_eh_cartao_valido(m.group()) for m in _CARTAO_RE.finditer(texto))


def _tem_cpf(texto: str) -> bool:
    return any(_eh_cpf(m.group()) for m in _CPF_RE.finditer(texto))


def _tem_dado_sensivel_critico(texto: str) -> bool:
    """CPF/RG/cartão — dados que nunca devem sair na resposta. CEP fica de
    fora daqui (baixo risco, mas gera falso positivo com mais frequência;
    ver `anonimizar_entrada`, que mascara CEP na entrada mesmo assim)."""
    return bool(_tem_cpf(texto) or _RG_RE.search(texto) or _tem_cartao(texto))


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
    texto = _SIMBOLO_EM_FORMA_DE_EMOJI_RE.sub("", resposta or "")
    texto = _EMOJI_RE.sub("", texto)
    texto = _SIMBOLOS_DIVERSOS_RE.sub(lambda m: m.group() if m.group() in _SIMBOLOS_DE_TEXTO else "", texto)
    # Emoji costuma vir cercado de espaço (ex.: "Oi! 👋 Tudo bem?" ou
    # "ter 💅. Time"); depois de removê-lo, limpa o espaço órfão antes de
    # pontuação e o espaço duplo que sobra.
    texto = _ESPACO_ANTES_DE_PONTUACAO_RE.sub(r"\1", texto)
    texto = _ESPACOS_REPETIDOS_RE.sub(" ", texto)
    return texto.strip()


def anonimizar_entrada(mensagem: str) -> str:
    """Remove/mascara dados sensíveis da mensagem do usuário antes de logar."""
    texto = mensagem or ""
    texto = _CPF_RE.sub(lambda m: "[CPF]" if _eh_cpf(m.group()) else m.group(), texto)
    texto = _RG_RE.sub("[RG]", texto)
    texto = _EMAIL_RE.sub("[EMAIL]", texto)
    texto = _CARTAO_RE.sub(lambda m: "[CARTAO]" if _eh_cartao_valido(m.group()) else m.group(), texto)
    texto = _CEP_RE.sub(_mascarar_cep, texto)
    texto = _TELEFONE_RE.sub("[TELEFONE]", texto)
    return texto
