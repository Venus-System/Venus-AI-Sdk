"""Nós dos agentes especialistas: produto, ingrediente, rotina e FAQ."""

from __future__ import annotations

import json
import logging
from typing import Any

from venus_sdk.flows.agente_mcp import montar_agente_mcp
from venus_sdk.llm.models import get_llm_especialista
from venus_sdk.prompts.faq import FAQ_PROMPT_COMPLETO
from venus_sdk.prompts.ingrediente import ESP_INGREDIENTE_PROMPT_COMPLETO
from venus_sdk.prompts.produto import ESP_PRODUTO_PROMPT_COMPLETO
from venus_sdk.prompts.rotina import ROTINA_PROMPT_COMPLETO
from venus_sdk.state import EstadoVenus

logger = logging.getLogger(__name__)

# Cada agente ReAct é montado sob demanda (uma vez) e reaproveitado entre
# chamadas — montá-lo carrega as tools MCP, que fazem I/O na primeira vez.
_agentes_cache: dict[str, Any] = {}


def _agente(nome: str, prompt: str) -> Any:
    if nome not in _agentes_cache:
        _agentes_cache[nome] = montar_agente_mcp(get_llm_especialista(), prompt=prompt)
    return _agentes_cache[nome]


def _montar_entrada(estado: EstadoVenus) -> str:
    """Monta o protocolo de entrada do especialista a partir do roteador,
    incluindo o feedback do Agente Juiz quando esta é uma nova tentativa
    (ver `nodes/juiz.py`)."""
    partes = [
        f"ROUTE={estado.get('rota')}",
        f"PERGUNTA_ORIGINAL={estado.get('pergunta_original') or estado.get('mensagem_usuario', '')}",
    ]
    memorias = estado.get("memorias_usuario")
    if memorias:
        partes.append(f"MEMORIA_USUARIO={json.dumps(memorias, ensure_ascii=False)}")
    feedback = estado.get("feedback_juiz")
    if feedback:
        partes.append(
            "OBSERVAÇÃO (Agente Juiz reprovou a tentativa anterior — corrija "
            f"antes de responder): {feedback}"
        )
    return "\n".join(partes)


def _resposta_agente(agente: Any, entrada: str) -> str:
    resultado = agente.invoke({"messages": [("human", entrada)]})
    return resultado["messages"][-1].content


def _executar_especialista(estado: EstadoVenus, nome: str, prompt: str) -> EstadoVenus:
    entrada = _montar_entrada(estado)
    texto = _resposta_agente(_agente(nome, prompt), entrada)

    try:
        resposta_json = json.loads(texto)
    except (TypeError, ValueError):
        logger.warning("Especialista %s não devolveu JSON válido: %r", nome, texto)
        resposta_json = {
            "dominio": nome,
            "intencao": "erro_formato",
            "resposta": "Não consegui estruturar uma resposta válida para essa pergunta.",
            "recomendacao": "",
            "fontes_usadas": [],
        }

    return {"resposta_especialista": resposta_json}


def no_agente_produto(estado: EstadoVenus) -> EstadoVenus:
    """Roda o agente MCP de produto e grava o JSON em `resposta_especialista`."""
    return _executar_especialista(estado, "produto", ESP_PRODUTO_PROMPT_COMPLETO)


def no_agente_ingrediente(estado: EstadoVenus) -> EstadoVenus:
    """Idem, usando `ESP_INGREDIENTE_PROMPT_COMPLETO`."""
    return _executar_especialista(estado, "ingrediente", ESP_INGREDIENTE_PROMPT_COMPLETO)


def no_agente_rotina(estado: EstadoVenus) -> EstadoVenus:
    """Idem, usando `ROTINA_PROMPT_COMPLETO`."""
    return _executar_especialista(estado, "rotina", ROTINA_PROMPT_COMPLETO)


def no_agente_faq(estado: EstadoVenus) -> EstadoVenus:
    """Usa `FAQ_PROMPT_COMPLETO`; grava a resposta final direto em
    `resposta_final` (o FAQ não passa pelo Agente Juiz)."""
    entrada = _montar_entrada(estado)
    texto = _resposta_agente(_agente("faq", FAQ_PROMPT_COMPLETO), entrada)
    return {"resposta_final": texto}
