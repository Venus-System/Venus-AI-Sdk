from __future__ import annotations

from functools import lru_cache

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


@lru_cache(maxsize=1)
def get_llm_gemini() -> BaseChatModel:
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
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
