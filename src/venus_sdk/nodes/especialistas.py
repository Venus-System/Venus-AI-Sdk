"""Nós dos agentes especialistas: produto, ingrediente, rotina e FAQ."""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from langchain_core.messages import ToolMessage

from venus_sdk.flows.agente_mcp import montar_agente_mcp
from venus_sdk.llm.models import extrair_texto_resposta, get_llm_especialista
from venus_sdk.prompts.faq import FAQ_PROMPT_COMPLETO
from venus_sdk.prompts.ingrediente import ESP_INGREDIENTE_PROMPT_COMPLETO
from venus_sdk.prompts.produto import ESP_PRODUTO_PROMPT_COMPLETO
from venus_sdk.prompts.rotina import ROTINA_PROMPT_COMPLETO
from venus_sdk.state import EstadoVenus
from venus_sdk.tools.compartilhadas import montar_tools_compartilhadas
from venus_sdk.tools.ingrediente import montar_tools_ingrediente
from venus_sdk.tools.produto import montar_tools_produto

logger = logging.getLogger(__name__)

# Fallback pra quando o especialista falha por completo (ex.: Gemini E Groq
# indisponíveis ao mesmo tempo — ver nota em `_resposta_agente`) — evita que
# um erro de provedor de LLM derrube a conversa inteira sem resposta
# nenhuma; o Agente Juiz reprova isto naturalmente (fontes_usadas vazio),
# então depois de `MAX_TENTATIVAS_JUIZ` o orquestrador ainda comunica o
# problema com transparência (ver `nodes/juiz.py`/`nodes/orquestrador.py`).
_RESPOSTA_ESPECIALISTA_FALLBACK = (
    "Não consegui consultar as informações necessárias agora — pode "
    "tentar de novo em instantes?"
)

# Mesmo espírito de `_RESPOSTA_ESPECIALISTA_FALLBACK`, mas pro FAQ — que
# escreve direto em `resposta_final` e não passa pelo Agente Juiz (ver
# `flows/venus_flow.py`), então precisa do próprio texto de fallback.
_RESPOSTA_FAQ_FALLBACK = (
    "Não consegui buscar essa informação agora — pode tentar de novo em instantes?"
)

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
    # ID inteiro do Postgres (distinto do usuario_id string da memória de
    # longo prazo, ver `state.py`) — só incluído quando existe de verdade,
    # nunca inventado aqui; ver `IDENTIFICADOR_USUARIO_NOTA` em
    # `prompts/comum.py` pro protocolo completo (o que o especialista deve
    # fazer com/sem esta linha).
    usuario_id_postgres = estado.get("usuario_id_postgres")
    if usuario_id_postgres is not None:
        partes.append(f"USER_ID_POSTGRES={usuario_id_postgres}")
    feedback = estado.get("feedback_juiz")
    if feedback:
        partes.append(
            "OBSERVAÇÃO (Agente Juiz reprovou a tentativa anterior — corrija "
            f"antes de responder): {feedback}"
        )
    return "\n".join(partes)


def _extrair_evidencias_tools(mensagens: list) -> list[dict[str, Any]]:
    """Extrai nome+retorno de cada `ToolMessage` da execução do agente ReAct
    — a evidência bruta que embasa (ou não) `resposta_especialista`, usada
    pelo Agente Juiz pra cruzar contra `fontes_usadas` (ver
    `nodes/juiz.py`). Sem isto, o Juiz só via o JSON final do especialista e
    não tinha como perceber quando uma tool citada não sustentava, de
    verdade, a afirmação feita (achado de um teste de conversa real em
    2026-09-10 — ver `EstadoVenus.evidencias_tools`)."""
    return [
        {"tool": mensagem.name, "resultado": mensagem.content}
        for mensagem in mensagens
        if isinstance(mensagem, ToolMessage)
    ]


async def _resposta_agente(agente: Any, entrada: str) -> tuple[str, list[dict[str, Any]]]:
    """Roda o agente via `ainvoke` — as tools de produto/ingrediente são
    async (asyncpg, ver `tools/produto.py`/`tools/ingrediente.py`).

    Precisa ser `await`ada dentro do MESMO event loop usado pra criar o
    `pool` (ver `nodes/especialistas.py::montar_no_agente_produto` e o
    exemplo em `examples/conversar_com_venus.py`) — nunca via
    `asyncio.run()` aqui dentro: um `asyncpg.Pool` fica preso ao loop onde
    foi criado, e `asyncio.run()` cria (e fecha) um loop novo a cada
    chamada, o que quebrava esse pool com `RuntimeError: Event loop is
    closed` assim que a segunda mensagem tentava reusá-lo. Por isso todo o
    grafo principal roda via `ainvoke`/`compilar_grafo_venus(...).ainvoke`
    — nós síncronos continuam funcionando nesse modo (o LangGraph os roda
    numa thread separada), então isso não exige mudar os outros nós.

    `extrair_texto_resposta` normaliza o `content` da última mensagem — o
    gemini-3.6-flash (usado por `get_llm_especialista()`) às vezes devolve
    uma lista de blocos (thought signature) em vez de string simples; sem
    isso, `json.loads()` em `_executar_especialista` falhava e descartava a
    resposta de verdade do especialista, caindo no fallback genérico de
    erro de formato.

    Devolve `(texto, evidencias_tools)` — ver `_extrair_evidencias_tools`."""
    resultado = await agente.ainvoke({"messages": [("human", entrada)]})
    mensagens = resultado["messages"]
    return extrair_texto_resposta(mensagens[-1]), _extrair_evidencias_tools(mensagens)


async def _executar_especialista(estado: EstadoVenus, nome: str, agente: Any) -> EstadoVenus:
    """Roda `agente` (já montado, com as tools do domínio) e grava o JSON
    devolvido em `resposta_especialista` (ou um JSON de erro, se a saída não
    for JSON válido)."""
    entrada = _montar_entrada(estado)

    try:
        texto, evidencias = await _resposta_agente(agente, entrada)
    except Exception:
        # Gemini E o fallback Groq falharam (ou algo mais quebrou dentro do
        # agente ReAct) — nunca deixa isso subir cru até `.ainvoke()` do
        # grafo principal (ver `_RESPOSTA_ESPECIALISTA_FALLBACK`); vira um
        # JSON reprovável normalmente pelo Agente Juiz, não um crash.
        logger.exception("Especialista %s falhou ao chamar o LLM/tools", nome)
        return {
            "resposta_especialista": {
                "dominio": nome,
                "intencao": "erro_tecnico",
                "resposta": _RESPOSTA_ESPECIALISTA_FALLBACK,
                "recomendacao": "",
                "fontes_usadas": [],
            },
            "evidencias_tools": None,
        }

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

    return {"resposta_especialista": resposta_json, "evidencias_tools": evidencias}


def montar_no_agente_produto(pool: Any) -> Callable[[EstadoVenus], Awaitable[EstadoVenus]]:
    """Fábrica do nó do agente de Produto — recebe o `pool` do Postgres (ver
    `tools/produto.py`) e devolve o nó pronto pra registrar no grafo
    (`flows/venus_flow.py::montar_grafo_venus`).

    O agente ReAct só é montado (e as tools só exigem `pool` de verdade) no
    primeiro uso real do nó, nunca na montagem do grafo — mesmo espírito do
    `_agente()` acima, só que com cache por instância da fábrica (uma por
    `pool`) em vez de cache global por nome.

    O nó é `async def` (ver `_resposta_agente`) — o grafo precisa ser
    invocado via `.ainvoke()`/`.astream()`, nunca `.invoke()`, quando `pool`
    não for `None` (ver `examples/conversar_com_venus.py`).
    """
    cache: dict[str, Any] = {}

    def _agente_produto() -> Any:
        if "produto" not in cache:
            tools = montar_tools_produto(pool) + montar_tools_compartilhadas(pool)
            cache["produto"] = montar_agente_mcp(
                get_llm_especialista(), prompt=ESP_PRODUTO_PROMPT_COMPLETO, tools=tools
            )
        return cache["produto"]

    async def no_agente_produto(estado: EstadoVenus) -> EstadoVenus:
        """Roda o agente de produto (tools de Postgres) e grava o JSON em
        `resposta_especialista`."""
        return await _executar_especialista(estado, "produto", _agente_produto())

    return no_agente_produto


def montar_no_agente_ingrediente(pool: Any) -> Callable[[EstadoVenus], Awaitable[EstadoVenus]]:
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

    async def no_agente_ingrediente(estado: EstadoVenus) -> EstadoVenus:
        """Roda o agente de ingrediente (tools de Postgres) e grava o JSON
        em `resposta_especialista`."""
        return await _executar_especialista(estado, "ingrediente", _agente_ingrediente())

    return no_agente_ingrediente


async def no_agente_rotina(estado: EstadoVenus) -> EstadoVenus:
    """Idem, usando `ROTINA_PROMPT_COMPLETO` — ainda via o client MCP
    genérico (stub); tools de rotina (MongoDB) pendentes."""
    return await _executar_especialista(estado, "rotina", _agente("rotina", ROTINA_PROMPT_COMPLETO))


async def no_agente_faq(estado: EstadoVenus) -> EstadoVenus:
    """Usa `FAQ_PROMPT_COMPLETO`; grava a resposta final direto em
    `resposta_final` (o FAQ não passa pelo Agente Juiz). Ainda via o client
    MCP genérico (stub); tool `faq_retriever` (Qdrant) pendente."""
    entrada = _montar_entrada(estado)
    # `_agente(...)` fica FORA do try: enquanto `mcp/tools.py` for stub, ela
    # levanta `NotImplementedError` na montagem (ver `flows/agente_mcp.py`)
    # e isso deve propagar cru — é o sinal que `examples/conversar_com_venus.py`
    # espera pra imprimir "[ainda não implementado]", não uma falha de LLM.
    agente = _agente("faq", FAQ_PROMPT_COMPLETO)
    try:
        texto, _evidencias = await _resposta_agente(agente, entrada)
    except Exception:
        # Sem Agente Juiz depois do FAQ (ver `flows/venus_flow.py`) — se não
        # blindar aqui, uma falha de LLM (Gemini e Groq indisponíveis) sobe
        # crua até o `.ainvoke()` do grafo principal, igual ao caso resolvido
        # em `_executar_especialista`.
        logger.exception("Especialista FAQ falhou ao chamar o LLM/tools")
        return {"resposta_final": _RESPOSTA_FAQ_FALLBACK}
    return {"resposta_final": texto or _RESPOSTA_FAQ_FALLBACK}
