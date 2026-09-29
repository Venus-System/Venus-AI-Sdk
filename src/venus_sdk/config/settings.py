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
DATABASE_URL = os.getenv("DATABASE_URL")
# Aceita MONGODB_URI (nome do atributo) e MONGODB_URL (legado) — opcional.
MONGODB_URI = os.getenv("MONGODB_URI") or os.getenv("MONGODB_URL")
FAQ_DIR = os.getenv("FAQ_DIR") or str(BASE_DIR / "data" / "faq")

# Lidas direto do ambiente por quem usa (ver `.env.example`), todas opcionais:
# LLM_PROVIDER e LLM_CADEIA_* (llm/models.py), TAVILY_API_KEY (rag/web.py),
# MCP_SERVERS (mcp/tools.py) e A2A_AGENTES_EXTERNOS (a2a_client.py).

# Basta UMA chave de LLM (as cadeias de fallback pulam os provedores sem chave).
CHAVES_LLM = {"MISTRAL_API_KEY": MISTRAL_API_KEY, "GROQ_API_KEY": GROQ_API_KEY, "GEMINI_API_KEY": GEMINI_API_KEY}


def validar_config(exigir_banco: bool = False) -> list[str]:
    """Devolve a lista de problemas de configuração (vazia = tudo certo).
    `exigir_banco=True` também cobra `DATABASE_URL` (Postgres das tools)."""
    problemas = []
    if exigir_banco and not DATABASE_URL:
        problemas.append("Variável ausente no .env: DATABASE_URL")
    if not any(CHAVES_LLM.values()):
        problemas.append("Nenhuma chave de LLM no .env: defina MISTRAL_API_KEY, GROQ_API_KEY e/ou GEMINI_API_KEY")
    return problemas
