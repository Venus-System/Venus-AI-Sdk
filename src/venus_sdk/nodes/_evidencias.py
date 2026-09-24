"""Leitura das evidências das tools (`EstadoVenus.evidencias_tools`), usada
pelos nós que precisam do retorno bruto de uma tool específica."""

from __future__ import annotations

import json
from typing import Any


def dados_da_evidencia(evidencias: list[dict] | None, tool: str) -> Any:
    """Resultado decodificado da PRIMEIRA chamada de `tool`, ou `None` se ela
    não foi chamada ou devolveu algo que não é JSON.

    O resultado pode chegar como JSON serializado mais de uma vez (a tool
    devolve um dict, o `ToolMessage` guarda o texto dele), por isso decodifica
    até deixar de ser string."""
    for evidencia in evidencias or []:
        if evidencia.get("tool") != tool:
            continue
        dados = evidencia.get("resultado")
        try:
            while isinstance(dados, str):
                dados = json.loads(dados)
        except ValueError:
            return None
        return dados
    return None
