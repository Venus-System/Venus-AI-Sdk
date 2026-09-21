from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq

from venus_sdk.config.settings import GEMINI_API_KEY, GROQ_API_KEY, MISTRAL_API_KEY

# ==============================================================================
# MODELOS E AGENTES  (sem checkpointer — a memória fica no grafo)
# ==============================================================================
#
# Cada client é criado sob demanda, na primeira chamada de cada get_llm_*(),
# e reaproveitado depois (via lru_cache) — não na hora do import deste
# módulo. Isso evita que só importar `venus_sdk.llm.models` (o que os nós
# fazem no topo do arquivo) já exija GEMINI_API_KEY/GROQ_API_KEY presentes,
# algo que quebra em qualquer ambiente sem `.env`/secrets configurados, como
# o runner do CI.


def extrair_texto_resposta(resposta: Any) -> str:
    """Normaliza `AIMessage.content` pra string simples.

    `get_llm_gemini()`/`get_llm_especialista()` (gemini-3.6-flash, ver
    abaixo) às vezes devolvem `content` como uma LISTA de blocos —
    `[{"type": "text", "text": "...", "extras": {"signature": "..."}}]`,
    a "thought signature" desse modelo — em vez da string simples que
    `gemini-2.5-flash` devolvia. Chamar `.strip()`/`json.loads()` direto
    nisso quebra com `AttributeError`/`TypeError` (visto de verdade rodando
    o grafo completo em 2026-09-08). Extrai só o texto de cada bloco
    (ignora blocos sem `"text"`, como o de assinatura) e concatena — usada
    em todo lugar que lê `resposta.content` como texto (roteador, juiz,
    orquestrador, memória, especialistas).
    """
    conteudo = resposta.content
    if isinstance(conteudo, str):
        return conteudo
    if isinstance(conteudo, list):
        partes = [
            bloco if isinstance(bloco, str) else bloco.get("text", "")
            for bloco in conteudo
            if isinstance(bloco, str) or isinstance(bloco, dict)
        ]
        return "".join(partes)
    return str(conteudo) if conteudo else ""


@lru_cache(maxsize=1)
def get_llm_gemini() -> BaseChatModel:
    return ChatGoogleGenerativeAI(
        # gemini-2.5-flash foi descontinuado pro Google pra contas novas
        # (404 NOT_FOUND em produção, 2026-09-08) — substituído conforme a
        # própria mensagem de erro da API.
        #
        # Sem temperature/top_p de propósito: gemini-3.6-flash usa sampling
        # fixo e ignora os dois (UserWarning do langchain_google_genai a
        # cada chamada se passados) — omitir não muda o comportamento, só
        # tira o warning.
        model="gemini-3.6-flash",
        api_key=GEMINI_API_KEY,
        # O padrão do client é max_retries=6 SEM timeout: numa cota estourada
        # (429) ou rede lenta, uma única chamada ficava minutos "travada" em
        # silêncio em vez de cair no fallback do Groq. Falha rápido.
        max_retries=1,
        timeout=30,
    )


@lru_cache(maxsize=1)
def get_llm_groq() -> BaseChatModel:
    return ChatGroq(
        model="openai/gpt-oss-120b",
        temperature=0.7,
        api_key=GROQ_API_KEY,
        request_timeout=45,
        max_retries=2,
    )


def provedor_principal() -> str:
    """Provedor PRINCIPAL da cadeia padrão dos especialistas/orquestrador:
    `LLM_PROVIDER` ("groq" ou "gemini"; padrão "groq"). Só define a ORDEM da
    cadeia padrão — `LLM_CADEIA_ESPECIALISTA` (abaixo) tem prioridade.

    Por que o padrão é Groq: o plano gratuito do Gemini tem só 20
    requisições/DIA por modelo (`429 RESOURCE_EXHAUSTED`), e cada pergunta
    gasta 3+ chamadas."""
    return os.getenv("LLM_PROVIDER", "groq").strip().lower()


# ------------------------------------------------------------------------------
# CADEIAS DE FALLBACK
# ------------------------------------------------------------------------------
# Uma cadeia é uma lista ordenada de "provedor:modelo" — o 1º responde e, se
# falhar (cota, rede, 400 de tool call), o 2º assume, depois o 3º... Configure
# por variável de ambiente (separada por vírgula), ex.:
#
#   LLM_CADEIA_ESPECIALISTA=groq:openai/gpt-oss-120b,gemini:gemini-3.6-flash,groq:llama-3.3-70b-versatile
#   LLM_CADEIA_RAPIDO=groq:openai/gpt-oss-20b,groq:llama-3.1-8b-instant,gemini:gemini-3.6-flash
#
# Entradas cujo provedor não tem chave no `.env` são puladas.
# No plano gratuito desta conta: small/medium/magistral têm 0 req/min (429) e o large dá 403;
# ministral-14b (30 req/min, ~900K tok/min) e ministral-8b (188 req/min, ~600K tok/min) funcionam.
_MISTRAL = ["mistral:ministral-14b-latest", "mistral:ministral-8b-latest"]
_CADEIA_PADRAO_ESPECIALISTA = {
    "groq": ["groq:openai/gpt-oss-120b", *_MISTRAL, "groq:openai/gpt-oss-20b", "gemini:gemini-3.6-flash"],
    "gemini": ["gemini:gemini-3.6-flash", *_MISTRAL, "groq:openai/gpt-oss-120b", "groq:openai/gpt-oss-20b"],
}
_CADEIA_PADRAO_RAPIDO = ["mistral:ministral-14b-latest", "mistral:ministral-8b-latest", "groq:openai/gpt-oss-20b", "groq:openai/gpt-oss-120b",
                         "gemini:gemini-3.6-flash"]

_CHAVES = {"groq": lambda: GROQ_API_KEY, "gemini": lambda: GEMINI_API_KEY, "mistral": lambda: MISTRAL_API_KEY}


def _criar_modelo(provedor: str, modelo: str, *, rapido: bool = False) -> BaseChatModel:
    """Instancia UM elo da cadeia, com timeouts curtos (falhar rápido é o que
    permite o fallback entrar em vez de a conversa ficar parada)."""
    if provedor == "gemini":
        # Sem temperature/top_p: gemini-3.x usa sampling fixo (ver get_llm_gemini).
        return ChatGoogleGenerativeAI(model=modelo, api_key=GEMINI_API_KEY, max_retries=1, timeout=30)
    if provedor == "mistral":
        from langchain_mistralai import ChatMistralAI  # import tardio: só quem usa Mistral precisa do pacote

        return ChatMistralAI(model=modelo, api_key=MISTRAL_API_KEY, temperature=0.0 if rapido else 0.3,
                             timeout=45 if rapido else 75, max_retries=2)
    if provedor == "groq":
        kw: dict[str, Any] = {"temperature": 0.0 if rapido else 0.7, "api_key": GROQ_API_KEY,
                              "request_timeout": 45 if rapido else 75, "max_retries": 0}  # 429 -> próximo elo da cadeia
        if "gpt-oss" in modelo and not rapido:
            # Especialista: raciocínio "medium" levava 20-30s por passo do agente ReAct.
            kw.update(reasoning_effort="low")
        if "gpt-oss" in modelo and rapido:
            # Modelo de raciocínio: sem limitar, às vezes gasta o budget "pensando" e devolve
            # content="" (ver `get_llm_rapido`).
            kw.update(reasoning_effort="low", max_tokens=1024)
        return ChatGroq(model=modelo, **kw)
    raise ValueError(f"Provedor de LLM desconhecido: {provedor!r} (use 'mistral', 'groq' ou 'gemini')")


def parse_cadeia(texto: str) -> list[tuple[str, str]]:
    """'groq:modelo, gemini:modelo' -> [('groq','modelo'), ('gemini','modelo')].
    Levanta ValueError em entrada malformada."""
    itens: list[tuple[str, str]] = []
    for bruto in (texto or "").split(","):
        bruto = bruto.strip()
        if not bruto:
            continue
        provedor, sep, modelo = bruto.partition(":")
        if not sep or not modelo.strip() or provedor.strip().lower() not in _CHAVES:
            raise ValueError(f"Entrada inválida na cadeia de LLMs: {bruto!r} (esperado 'mistral:<modelo>', 'groq:<modelo>' ou 'gemini:<modelo>')")
        itens.append((provedor.strip().lower(), modelo.strip()))
    return itens


def cadeia_configurada(env: str, padrao: list[str]) -> list[tuple[str, str]]:
    """Cadeia de `env` (ou o padrão), sem os provedores sem chave de API e sem repetidos."""
    itens = parse_cadeia(os.getenv(env) or ",".join(padrao))
    vistos, saida = set(), []
    for item in itens:
        if item in vistos or not _CHAVES[item[0]]():
            continue
        vistos.add(item)
        saida.append(item)
    return saida


def _montar_cadeia(env: str, padrao: list[str], *, rapido: bool) -> BaseChatModel:
    itens = cadeia_configurada(env, padrao)
    if not itens:
        raise RuntimeError(f"Nenhum LLM utilizável em {env}: defina MISTRAL_API_KEY, GROQ_API_KEY e/ou GEMINI_API_KEY no .env.")
    modelos = [_criar_modelo(p, m, rapido=rapido) for p, m in itens]
    return modelos[0].with_fallbacks(modelos[1:]) if len(modelos) > 1 else modelos[0]


def cadeia_especialista() -> list[tuple[str, str]]:
    return cadeia_configurada("LLM_CADEIA_ESPECIALISTA", _CADEIA_PADRAO_ESPECIALISTA.get(provedor_principal(), _CADEIA_PADRAO_ESPECIALISTA["groq"]))


def cadeia_rapida() -> list[tuple[str, str]]:
    return cadeia_configurada("LLM_CADEIA_RAPIDO", _CADEIA_PADRAO_RAPIDO)


@lru_cache(maxsize=1)
def get_llm_especialista() -> BaseChatModel:
    """Especialistas/orquestrador: cadeia com vários fallbacks (ver acima)."""
    return _montar_cadeia(
        "LLM_CADEIA_ESPECIALISTA",
        _CADEIA_PADRAO_ESPECIALISTA.get(provedor_principal(), _CADEIA_PADRAO_ESPECIALISTA["groq"]),
        rapido=False,
    )


@lru_cache(maxsize=1)
def get_llm_rapido() -> BaseChatModel:
    """Roteador/juiz/memória (classificação curta): cadeia rápida com fallbacks.

    O gpt-oss-20b é um modelo de raciocínio: sem `reasoning_effort="low"` +
    `max_tokens` ele às vezes gasta o budget inteiro pensando e devolve
    content="" (visto de verdade: finish_reason="length"), o que fazia o
    roteador cair no fallback genérico sem relação com a mensagem."""
    return _montar_cadeia("LLM_CADEIA_RAPIDO", _CADEIA_PADRAO_RAPIDO, rapido=True)
