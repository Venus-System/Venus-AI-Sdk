from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq

from venus_sdk.config.settings import GEMINI_API_KEY, GROQ_API_KEY

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
        model="gemini-3.6-flash",
        temperature=0.7,
        top_p=0.95,
        api_key=GEMINI_API_KEY,
    )


@lru_cache(maxsize=1)
def get_llm_groq() -> BaseChatModel:
    return ChatGroq(
        model="openai/gpt-oss-120b",
        temperature=0.7,
        api_key=GROQ_API_KEY,
    )


@lru_cache(maxsize=1)
def get_llm_especialista() -> BaseChatModel:
    # se o Gemini falhar, o Groq assume
    return get_llm_gemini().with_fallbacks([get_llm_groq()])


@lru_cache(maxsize=1)
def get_llm_rapido() -> BaseChatModel:
    # reasoning_effort="low" + max_tokens: o gpt-oss-20b é um modelo de
    # raciocínio (pensa "por dentro" antes de responder) e, sem isso, às
    # vezes gasta o budget inteiro de tokens pensando e devolve content=""
    # (visto de verdade: finish_reason="length" com ~2046 de ~2048 tokens
    # em reasoning, pra uma entrada tão simples quanto "eu te amo" — daí o
    # fallback genérico em nodes/roteador.py aparecer sem relação com a
    # mensagem). Roteador/Juiz são classificação/validação curtas, não
    # precisam de raciocínio profundo — "low" resolve isso na prática.
    return ChatGroq(
        model="openai/gpt-oss-20b",
        temperature=0.0,
        reasoning_effort="low",
        max_tokens=1024,
        api_key=GROQ_API_KEY,
    )
