import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR     = Path(__file__).resolve().parents[3]   # .../settings.py -> config -> venus -> src -> raiz do projeto
# DATA_DIR     = BASE_DIR / "data"
# FRONTEND_DIR = BASE_DIR / "frontend"
# FAQ_PDF_PATH = DATA_DIR / "FAQ_assessor_v1.1.pdf"


load_dotenv(BASE_DIR / ".env")          # o único load_dotenv() do projeto
# PDF_PATH = os.getenv("PDF_PATH")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY")
# DATABASE_URL   = os.getenv("#")
# MONGODB_URI    = os.getenv("MONGODB_URL", "#")

OBRIGATORIAS = {
    "GEMINI_API_KEY": GEMINI_API_KEY,
   "GROQ_API_KEY":   GROQ_API_KEY,
    # "DATABASE_URL":   DATABASE_URL,
   # "MONGODB_URI":    MONGODB_URI,
}

def validar_config() -> list[str]:
    """Devolve a lista de problemas de configuração (vazia = tudo certo)."""
    problemas = []
    for nome, valor in OBRIGATORIAS.items():
        if not valor:
            problemas.append(f"Variável ausente no .env: {nome}")
    return problemas