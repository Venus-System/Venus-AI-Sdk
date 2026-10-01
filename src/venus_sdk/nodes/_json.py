"""Leitura tolerante do JSON que os LLMs devolvem (especialistas e extrator de
memória)."""

from __future__ import annotations

import json
import re
from typing import Any

from venus_sdk.texto import remover_acentos

_CERCA_MARKDOWN_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _normalizar_chaves(dados: dict[str, Any]) -> dict[str, Any]:
    """Modelos menores escrevem "domínio"/"intenção" (com acento): normaliza as
    chaves do objeto de topo para o contrato (`dominio`, `intencao`...)."""
    return {remover_acentos(str(chave)): valor for chave, valor in dados.items()}


def _candidatos_a_json(texto: str) -> list[str]:
    """O texto como veio, sem a cerca ```json``` e só o trecho entre a 1ª `{` e a última `}`."""
    bruto = (texto or "").strip()
    candidatos = [bruto, _CERCA_MARKDOWN_RE.sub("", bruto).strip()]
    inicio, fim = bruto.find("{"), bruto.rfind("}")
    if inicio != -1 and fim > inicio:
        candidatos.append(bruto[inicio : fim + 1])
    return candidatos


def extrair_objeto_json(texto: str) -> dict[str, Any]:
    """O objeto JSON do texto, tolerando o que os LLMs costumam fazer: cerca
    ```json```, frase antes/depois, quebra de linha DENTRO de string (inválido
    no modo estrito) e nome de campo acentuado. Levanta `ValueError` se não
    houver um OBJETO JSON — lista, número ou texto soltos não servem."""
    for candidato in _candidatos_a_json(texto):
        try:
            dados = json.loads(candidato, strict=False)
        except ValueError:
            continue
        if isinstance(dados, dict):
            return _normalizar_chaves(dados)
    raise ValueError("nenhum objeto JSON na resposta")
