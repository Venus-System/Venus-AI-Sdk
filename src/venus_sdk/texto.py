"""Utilitários de texto compartilhados pelo SDK."""

from __future__ import annotations

import unicodedata
from typing import Any


def remover_acentos(texto: str) -> str:
    """Remove os acentos (marcas combinantes) de `texto`, sem mexer em
    maiúsculas nem em espaços: 'Hialurônico' -> 'Hialuronico'."""
    decomposto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")


def texto_de_blocos(saida: Any) -> str:
    """Texto do retorno de uma tool MCP: string ou lista de blocos `{"type": "text", ...}`."""
    if isinstance(saida, str):
        return saida
    if isinstance(saida, (list, tuple)):
        return "\n".join(str(bloco.get("text") or "") if isinstance(bloco, dict) else str(bloco) for bloco in saida)
    return str(saida or "")
