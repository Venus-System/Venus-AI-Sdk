"""Configuração do SDK lida do `.env` (na raiz do projeto) e do ambiente.

É o único lugar que chama `load_dotenv()`; o resto do SDK importa as
constantes daqui."""

import os
from pathlib import Path

from dotenv import load_dotenv

# .../settings.py -> config -> venus_sdk -> src -> raiz do projeto
BASE_DIR = Path(__file__).resolve().parents[3]

load_dotenv(BASE_DIR / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq")  # provedor principal: "groq" ou "gemini" (ver llm/models.py)
DATABASE_URL = os.getenv("DATABASE_URL")
# Aceita MONGODB_URI (nome do atributo) e MONGODB_URL (legado) — opcional.
MONGODB_URI = os.getenv("MONGODB_URI") or os.getenv("MONGODB_URL")
QDRANT_URL = os.getenv("QDRANT_URL")  # opcional — o RAG padrão é local (data/faq)
QDRANT_API = os.getenv("QDRANT_API")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")  # opcional — sem ela a busca web usa DuckDuckGo
FAQ_DIR = os.getenv("FAQ_DIR") or str(BASE_DIR / "data" / "faq")
MCP_SERVERS = os.getenv("MCP_SERVERS")  # JSON opcional (ver mcp/tools.py)
A2A_AGENTES_EXTERNOS = os.getenv("A2A_AGENTES_EXTERNOS")  # JSON opcional {"nome": "url"}

# Basta UMA chave de LLM (as cadeias de fallback pulam os provedores sem chave).
CHAVES_LLM = {"MISTRAL_API_KEY": MISTRAL_API_KEY, "GROQ_API_KEY": GROQ_API_KEY, "GEMINI_API_KEY": GEMINI_API_KEY}

# Variáveis sempre obrigatórias, cobradas por `validar_config` — hoje nenhuma.
# DATABASE_URL é exigida só para produto/ingrediente/rotina (ver
# `validar_config(exigir_banco=True)`); MONGODB_URI, QDRANT_*, TAVILY_API_KEY,
# MCP_SERVERS e A2A_AGENTES_EXTERNOS são opcionais.
OBRIGATORIAS: dict = {}


def validar_config(exigir_banco: bool = False) -> list[str]:
    """Devolve a lista de problemas de configuração (vazia = tudo certo).
    `exigir_banco=True` também cobra `DATABASE_URL` (Postgres das tools)."""
    problemas = []
    if exigir_banco and not DATABASE_URL:
        problemas.append("Variável ausente no .env: DATABASE_URL")
    if not any(CHAVES_LLM.values()):
        problemas.append("Nenhuma chave de LLM no .env: defina MISTRAL_API_KEY, GROQ_API_KEY e/ou GEMINI_API_KEY")
    for nome, valor in OBRIGATORIAS.items():
        if not valor:
            problemas.append(f"Variável ausente no .env: {nome}")
    return problemas
