"""Modelos de linguagem do Venus: clients por provedor e as cadeias de
fallback usadas pelos nós (especialistas/orquestrador e roteador/juiz/memória)."""

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

    O gemini-3.6-flash (um dos elos das cadeias abaixo) às vezes devolve `content` como uma LISTA de blocos —
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
            if isinstance(bloco, (str, dict))
        ]
        return "".join(partes)
    return str(conteudo) if conteudo else ""


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
_CADEIA_PADRAO_RAPIDO = [*_MISTRAL, "groq:openai/gpt-oss-20b", "groq:openai/gpt-oss-120b", "gemini:gemini-3.6-flash"]

# Lidas na hora da chamada (e não copiadas aqui) para refletir o valor atual
# das variáveis do módulo.
_CHAVES = {"groq": lambda: GROQ_API_KEY, "gemini": lambda: GEMINI_API_KEY, "mistral": lambda: MISTRAL_API_KEY}

# Timeouts (s) por elo: curtos no modelo rápido (classificação), maiores no especialista.
_TIMEOUT_RAPIDO = 45
_TIMEOUT_ESPECIALISTA = 75
_TIMEOUT_GEMINI = 30
_MAX_TOKENS_RAPIDO_COM_RACIOCINIO = 1024


def _criar_gemini(modelo: str) -> BaseChatModel:
    # Sem temperature/top_p: gemini-3.x usa sampling fixo e ignora os dois
    # (só geraria UserWarning a cada chamada). max_retries=1 + timeout curto:
    # o padrão do client (6 retries, sem timeout) prendia a chamada por
    # minutos numa cota estourada em vez de cair no próximo elo da cadeia.
    return ChatGoogleGenerativeAI(model=modelo, api_key=GEMINI_API_KEY, max_retries=1, timeout=_TIMEOUT_GEMINI)


def _temperatura(rapido: bool, temperatura: float | None, padrao_especialista: float) -> float:
    if temperatura is not None:
        return temperatura
    return 0.0 if rapido else padrao_especialista


def _criar_mistral(modelo: str, *, rapido: bool, temperatura: float | None = None) -> BaseChatModel:
    from langchain_mistralai import ChatMistralAI  # import tardio: só quem usa Mistral precisa do pacote

    return ChatMistralAI(
        model=modelo,
        api_key=MISTRAL_API_KEY,
        temperature=_temperatura(rapido, temperatura, 0.3),
        timeout=_TIMEOUT_RAPIDO if rapido else _TIMEOUT_ESPECIALISTA,
        max_retries=2,
    )


def _criar_groq(modelo: str, *, rapido: bool, temperatura: float | None = None) -> BaseChatModel:
    parametros: dict[str, Any] = {
        "temperature": _temperatura(rapido, temperatura, 0.7),
        "api_key": GROQ_API_KEY,
        "request_timeout": _TIMEOUT_RAPIDO if rapido else _TIMEOUT_ESPECIALISTA,
        "max_retries": 0,  # 429 -> próximo elo da cadeia
    }
    if "gpt-oss" in modelo:
        # Especialista: raciocínio "medium" levava 20-30s por passo do agente ReAct.
        parametros["reasoning_effort"] = "low"
        if rapido:
            # Modelo de raciocínio: sem limitar, às vezes gasta o budget "pensando" e devolve
            # content="" (ver `get_llm_rapido`).
            parametros["max_tokens"] = _MAX_TOKENS_RAPIDO_COM_RACIOCINIO
    return ChatGroq(model=modelo, **parametros)


def _criar_modelo(
    provedor: str, modelo: str, *, rapido: bool = False, temperatura: float | None = None
) -> BaseChatModel:
    """Instancia UM elo da cadeia, com timeouts curtos (falhar rápido é o que
    permite o fallback entrar em vez de a conversa ficar parada).
    `temperatura` sobrepõe a padrão (não se aplica ao Gemini, de sampling fixo)."""
    if provedor == "gemini":
        return _criar_gemini(modelo)
    if provedor == "mistral":
        return _criar_mistral(modelo, rapido=rapido, temperatura=temperatura)
    if provedor == "groq":
        return _criar_groq(modelo, rapido=rapido, temperatura=temperatura)
    raise ValueError(f"Provedor de LLM desconhecido: {provedor!r} (use 'mistral', 'groq' ou 'gemini')")


def parse_cadeia(texto: str) -> list[tuple[str, str]]:
    """'groq:modelo, gemini:modelo' -> [('groq','modelo'), ('gemini','modelo')].
    Levanta ValueError em entrada malformada."""
    itens: list[tuple[str, str]] = []
    for entrada in (texto or "").split(","):
        entrada = entrada.strip()
        if not entrada:
            continue
        provedor, separador, modelo = entrada.partition(":")
        provedor, modelo = provedor.strip().lower(), modelo.strip()
        if not separador or not modelo or provedor not in _CHAVES:
            raise ValueError(
                f"Entrada inválida na cadeia de LLMs: {entrada!r} "
                "(esperado 'mistral:<modelo>', 'groq:<modelo>' ou 'gemini:<modelo>')"
            )
        itens.append((provedor, modelo))
    return itens


def cadeia_configurada(env: str, padrao: list[str]) -> list[tuple[str, str]]:
    """Cadeia de `env` (ou o padrão), sem os provedores sem chave de API e sem repetidos."""
    utilizaveis: list[tuple[str, str]] = []
    for provedor, modelo in parse_cadeia(os.getenv(env) or ",".join(padrao)):
        if (provedor, modelo) in utilizaveis or not _CHAVES[provedor]():
            continue
        utilizaveis.append((provedor, modelo))
    return utilizaveis


def _montar_cadeia(env: str, padrao: list[str], *, rapido: bool, temperatura: float | None = None) -> BaseChatModel:
    itens = cadeia_configurada(env, padrao)
    if not itens:
        raise RuntimeError(
            f"Nenhum LLM utilizável em {env}: defina MISTRAL_API_KEY, GROQ_API_KEY e/ou GEMINI_API_KEY no .env."
        )
    parametros: dict[str, Any] = {"rapido": rapido}
    if temperatura is not None:
        parametros["temperatura"] = temperatura
    principal, *fallbacks = [_criar_modelo(provedor, modelo, **parametros) for provedor, modelo in itens]
    return principal.with_fallbacks(fallbacks) if fallbacks else principal


def _cadeia_padrao_especialista() -> list[str]:
    return _CADEIA_PADRAO_ESPECIALISTA.get(provedor_principal(), _CADEIA_PADRAO_ESPECIALISTA["groq"])


def cadeia_especialista() -> list[tuple[str, str]]:
    return cadeia_configurada("LLM_CADEIA_ESPECIALISTA", _cadeia_padrao_especialista())


def cadeia_rapida() -> list[tuple[str, str]]:
    return cadeia_configurada("LLM_CADEIA_RAPIDO", _CADEIA_PADRAO_RAPIDO)


@lru_cache(maxsize=1)
def get_llm_especialista() -> BaseChatModel:
    """Especialistas/orquestrador: cadeia com vários fallbacks (ver acima)."""
    return _montar_cadeia("LLM_CADEIA_ESPECIALISTA", _cadeia_padrao_especialista(), rapido=False)


@lru_cache(maxsize=1)
def get_llm_orquestrador() -> BaseChatModel:
    """Orquestrador: mesmos modelos dos especialistas, com temperatura 0.

    O orquestrador só reescreve o JSON do especialista em tom de conversa;
    com a temperatura dos especialistas (0.7) ele "enfeitava" a resposta com
    fatos, dicas e elogios que não estavam no JSON (teste de 2026-09-26)."""
    return _montar_cadeia(
        "LLM_CADEIA_ESPECIALISTA", _cadeia_padrao_especialista(), rapido=False, temperatura=0.0
    )


def get_llm_juiz() -> BaseChatModel:
    """Agente Juiz: mesma cadeia dos especialistas, não a rápida.

    Julgar exige ler com cuidado o JSON do especialista contra o retorno
    bruto das tools. Com o modelo rápido o Juiz errava leituras simples
    (teste de 2026-09-26: disse que o FAQ citava "cinco dimensões" do score
    quando o trecho recuperado listava seis) e reprovava respostas corretas."""
    return get_llm_especialista()


@lru_cache(maxsize=1)
def get_llm_rapido() -> BaseChatModel:
    """Roteador/memória (classificação curta): cadeia rápida com fallbacks.

    O gpt-oss-20b é um modelo de raciocínio: sem `reasoning_effort="low"` +
    `max_tokens` ele às vezes gasta o budget inteiro pensando e devolve
    content="" (visto de verdade: finish_reason="length"), o que fazia o
    roteador cair no fallback genérico sem relação com a mensagem."""
    return _montar_cadeia("LLM_CADEIA_RAPIDO", _CADEIA_PADRAO_RAPIDO, rapido=True)
