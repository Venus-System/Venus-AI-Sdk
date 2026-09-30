"""Utilitários de texto compartilhados pelo SDK."""

from __future__ import annotations

import unicodedata


def remover_acentos(texto: str) -> str:
    """Remove os acentos (marcas combinantes) de `texto`, sem mexer em
    maiúsculas nem em espaços: 'Hialurônico' -> 'Hialuronico'."""
    decomposto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")
