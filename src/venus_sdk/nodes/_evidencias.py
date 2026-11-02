"""Leitura das evidências das tools (`EstadoVenus.evidencias_tools`), usada
pelos nós que precisam do retorno bruto de uma tool específica."""

from __future__ import annotations

import json
import os
from typing import Any


def dados_da_evidencia(evidencias: list[dict] | None, tool: str, **argumentos: Any) -> Any:
    """Resultado decodificado da PRIMEIRA chamada de `tool`, ou `None` se ela
    não foi chamada ou devolveu algo que não é JSON.

    Com `argumentos` (ex.: `ingredient_id=4315`), só considera chamadas feitas
    com esses valores — evidência sem `argumentos` registrados não casa.

    O resultado pode chegar como JSON serializado mais de uma vez (a tool
    devolve um dict, o `ToolMessage` guarda o texto dele), por isso decodifica
    até deixar de ser string."""
    for evidencia in evidencias or []:
        if evidencia.get("tool") != tool or not _chamada_com(evidencia, argumentos):
            continue
        dados = evidencia.get("resultado")
        try:
            while isinstance(dados, str):
                dados = json.loads(dados)
        except ValueError:
            return None
        return dados
    return None


def argumentos_da_evidencia(evidencias: list[dict] | None, tool: str) -> dict[str, Any]:
    """Argumentos da PRIMEIRA chamada de `tool` (`{}` se não houver)."""
    for evidencia in evidencias or []:
        if evidencia.get("tool") == tool:
            return evidencia.get("argumentos") or {}
    return {}


def rota_so_com_tools(rota: str | None) -> bool:
    """`VENUS_RESPOSTA_SO_DAS_TOOLS` (rotas separadas por vírgula; vazia desliga):
    nessas rotas o texto final é montado pelo código, só com o retorno das tools,
    para não haver afirmação sem fonte."""
    return bool(rota) and rota in os.getenv("VENUS_RESPOSTA_SO_DAS_TOOLS", "produto,ingrediente").split(",")


def alergias_declaradas(evidencias: list[dict] | None) -> list[str]:
    """Nomes das alergias que `get_user_allergies` devolveu (`[]` se nenhuma)."""
    dados = dados_da_evidencia(evidencias, "get_user_allergies")
    if not isinstance(dados, list):
        return []
    return [str(a.get("allergy_name")) for a in dados if isinstance(a, dict) and a.get("allergy_name")]


def _chamada_com(evidencia: dict, argumentos: dict[str, Any]) -> bool:
    if not argumentos:
        return True
    registrados = evidencia.get("argumentos")
    if not isinstance(registrados, dict):
        return False
    return all(str(registrados.get(nome)) == str(valor) for nome, valor in argumentos.items())
