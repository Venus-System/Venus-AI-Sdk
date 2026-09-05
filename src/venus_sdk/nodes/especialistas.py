"""Nós dos agentes especialistas: produto, ingrediente, rotina e FAQ."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from venus_sdk.flows.agente_mcp import montar_agente_mcp
from venus_sdk.llm.models import get_llm_especialista
from venus_sdk.prompts.faq import FAQ_PROMPT_COMPLETO
from venus_sdk.prompts.ingrediente import ESP_INGREDIENTE_PROMPT_COMPLETO
from venus_sdk.prompts.produto import ESP_PRODUTO_PROMPT_COMPLETO
from venus_sdk.prompts.rotina import ROTINA_PROMPT_COMPLETO
from venus_sdk.state import EstadoVenus
from venus_sdk.tools.compartilhadas import montar_tools_compartilhadas
from venus_sdk.tools.ingrediente import montar_tools_ingrediente
from venus_sdk.tools.produto import montar_tools_produto

logger = logging.getLogger(__name__)

# Cada agente ReAct é montado sob demanda (uma vez) e reaproveitado entre
# chamadas — montá-lo carrega as tools, que fazem I/O na primeira vez.
# Usado só por rotina/faq hoje, que ainda não têm tools reais (Mongo/Qdrant
# pendentes) — produto/ingrediente usam as fábricas por pool logo abaixo.
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
    """Roda o agente via `ainvoke`, sempre — as tools de produto/ingrediente
    são async (asyncpg, ver `tools/produto.py`/`tools/ingrediente.py`), então
    o agente ReAct delas só funciona por essa via. `asyncio.run()` faz a
    ponte: cria um loop novo pra essa chamada (o resto do grafo continua
    síncrono, chamado com `.invoke()`/`.ainvoke()` como hoje — funciona nos
    dois casos, já que o LangGraph roda nós síncronos numa thread separada
    quando o grafo inteiro é invocado de forma assíncrona)."""
    resultado = asyncio.run(agente.ainvoke({"messages": [("human", entrada)]}))
    return resultado["messages"][-1].content


def _executar_especialista(estado: EstadoVenus, nome: str, agente: Any) -> EstadoVenus:
    """Roda `agente` (já montado, com as tools do domínio) e grava o JSON
    devolvido em `resposta_especialista` (ou um JSON de erro, se a saída não
    for JSON válido)."""
    entrada = _montar_entrada(estado)
    texto = _resposta_agente(agente, entrada)

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


def montar_no_agente_produto(pool: Any) -> Callable[[EstadoVenus], EstadoVenus]:
    """Fábrica do nó do agente de Produto — recebe o `pool` do Postgres (ver
    `tools/produto.py`) e devolve o nó pronto pra registrar no grafo
    (`flows/venus_flow.py::montar_grafo_venus`).

    O agente ReAct só é montado (e as tools só exigem `pool` de verdade) no
    primeiro uso real do nó, nunca na montagem do grafo — mesmo espírito do
    `_agente()` acima, só que com cache por instância da fábrica (uma por
    `pool`) em vez de cache global por nome.
    """
    cache: dict[str, Any] = {}

    def _agente_produto() -> Any:
        if "produto" not in cache:
            tools = montar_tools_produto(pool) + montar_tools_compartilhadas(pool)
            cache["produto"] = montar_agente_mcp(
                get_llm_especialista(), prompt=ESP_PRODUTO_PROMPT_COMPLETO, tools=tools
            )
        return cache["produto"]

    def no_agente_produto(estado: EstadoVenus) -> EstadoVenus:
        """Roda o agente de produto (tools de Postgres) e grava o JSON em
        `resposta_especialista`."""
        return _executar_especialista(estado, "produto", _agente_produto())

    return no_agente_produto


def montar_no_agente_ingrediente(pool: Any) -> Callable[[EstadoVenus], EstadoVenus]:
    """Idem `montar_no_agente_produto`, para o agente de Ingrediente (ver
    `tools/ingrediente.py`)."""
    cache: dict[str, Any] = {}

    def _agente_ingrediente() -> Any:
        if "ingrediente" not in cache:
            tools = montar_tools_ingrediente(pool) + montar_tools_compartilhadas(pool)
            cache["ingrediente"] = montar_agente_mcp(
                get_llm_especialista(), prompt=ESP_INGREDIENTE_PROMPT_COMPLETO, tools=tools
            )
        return cache["ingrediente"]

    def no_agente_ingrediente(estado: EstadoVenus) -> EstadoVenus:
        """Roda o agente de ingrediente (tools de Postgres) e grava o JSON
        em `resposta_especialista`."""
        return _executar_especialista(estado, "ingrediente", _agente_ingrediente())

    return no_agente_ingrediente


def no_agente_rotina(estado: EstadoVenus) -> EstadoVenus:
    """Idem, usando `ROTINA_PROMPT_COMPLETO` — ainda via o client MCP
    genérico (stub); tools de rotina (MongoDB) pendentes."""
    return _executar_especialista(estado, "rotina", _agente("rotina", ROTINA_PROMPT_COMPLETO))


def no_agente_faq(estado: EstadoVenus) -> EstadoVenus:
    """Usa `FAQ_PROMPT_COMPLETO`; grava a resposta final direto em
    `resposta_final` (o FAQ não passa pelo Agente Juiz). Ainda via o client
    MCP genérico (stub); tool `faq_retriever` (Qdrant) pendente."""
    entrada = _montar_entrada(estado)
    texto = _resposta_agente(_agente("faq", FAQ_PROMPT_COMPLETO), entrada)
    return {"resposta_final": texto}
