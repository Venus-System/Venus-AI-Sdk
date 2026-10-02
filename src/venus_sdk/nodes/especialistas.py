"""Nós dos agentes especialistas: produto, ingrediente, rotina e FAQ (RAG)."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import SystemMessage, ToolMessage

from venus_sdk.flows.agente_mcp import montar_agente_mcp
from venus_sdk.llm.models import extrair_texto_resposta, get_llm_especialista
from venus_sdk.nodes._evidencias import dados_da_evidencia
from venus_sdk.nodes._json import extrair_objeto_json
from venus_sdk.nodes.agendamento import (
    INTENCAO_AGENDAMENTO,
    RESPOSTA_CANCELADO,
    eh_confirmacao,
    eh_negacao,
    executar_propostas,
    propostas_das_evidencias,
    propostas_validas,
)
from venus_sdk.prompts.comum import com_data_atual
from venus_sdk.prompts.faq import FAQ_PROMPT_COMPLETO
from venus_sdk.prompts.ingrediente import ESP_INGREDIENTE_PROMPT_COMPLETO
from venus_sdk.prompts.produto import ESP_PRODUTO_PROMPT_COMPLETO
from venus_sdk.prompts.rotina import ROTINA_PROMPT_COMPLETO
from venus_sdk.state import EstadoVenus
from venus_sdk.tools.compartilhadas import montar_tools_compartilhadas
from venus_sdk.tools.faq import montar_tools_faq
from venus_sdk.tools.ingrediente import montar_tools_ingrediente
from venus_sdk.tools.produto import montar_tools_produto
from venus_sdk.tools._identidade import usuario_da_conversa
from venus_sdk.tools.rotina import montar_tools_rotina

logger = logging.getLogger(__name__)

NoEspecialista = Callable[[EstadoVenus], Awaitable[EstadoVenus]]

# Resposta quando o especialista falha por completo (todos os LLMs da cadeia
# fora do ar): a conversa segue e o orquestrador comunica o problema.
_RESPOSTA_ESPECIALISTA_FALLBACK = (
    "Tive um probleminha pra buscar essas informações agora. Pode tentar "
    "de novo em instantes?"
)
_RESPOSTA_ERRO_FORMATO = "Não consegui estruturar uma resposta válida para essa pergunta."

# Teto de passos do agente ReAct por pergunta; cada rodada de tool gasta 2
# (LLM + tool) e o agente de ingrediente chega a consultar as 5 tools.
_LIMITE_PASSOS_AGENTE = 24
_TAMANHO_PREVIA_LOG = 80

# Adição também exige o verbo apontando PARA os favoritos ("coloca X nos
# favoritos", "marca como favorito") — "meus favoritos salvos", "favoritos da
# marca X" e "rotina incluindo meus favoritos" são leitura.
_ADICAO_DE_FAVORITO_RE = re.compile(
    r"\bfavoritar\b|^\s*favorit[ae]\b|\bmarc\w*\s+(isso\s+|ele\s+|ela\s+)?como\s+favorit"
    r"|\b(adicion\w*|inclu\w*|coloc\w*|salv\w*|guard\w*|bot[ae]\w*|p[oõ]e|p[oô]r)\b.{0,60}?"
    r"\b(a|à|ao|aos|n[oa]s?|em|para|pr[oa]s?|de)\s+(meus\s+|minhas\s+|minha\s+lista\s+de\s+)?favorit",
    re.IGNORECASE,
)
# Remoção exige o verbo apontando PARA os favoritos ("tira X dos meus
# favoritos") — "monta uma rotina com meus favoritos excluindo X" não é pedido
# de remoção e segue para o agente normalmente.
_REMOCAO_DE_FAVORITO_RE = re.compile(
    r"\bdesfavorit\w*|\b(remov\w*|tir[ae]\w*|exclu\w*|apag\w*)\b.{0,60}?\b(d[oae]s?)\s+(meus\s+)?favorit",
    re.IGNORECASE,
)

# A IA nunca altera os favoritos (nem adiciona, nem remove): o pedido é
# recusado sem passar pelo LLM — não há tool para isso, e deixar o LLM
# responder gerava recusas que o Juiz reprovava por "não ter fonte".
INTENCAO_NAO_SUPORTADA = "nao_suportado"
_RESPOSTA_ALTERAR_FAVORITO = (
    "Eu não mexo nos seus favoritos — adicionar ou remover produtos é com você, direto no app. "
    "Posso te mostrar os favoritos que você já tem ou montar uma rotina com eles?"
)


# --- entrada do especialista ---


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
    # Só entra quando existe de verdade (ver `IDENTIFICADOR_USUARIO_NOTA`).
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


# --- execução do agente ReAct ---


def _extrair_evidencias_tools(mensagens: list) -> list[dict[str, Any]]:
    """Nome, retorno e `argumentos` de cada `ToolMessage` da execução — a
    evidência que o Juiz confere. Os argumentos dizem de QUAL item é cada
    retorno quando o agente consulta mais de um (ex.: dois `ingredient_id`)."""
    argumentos_por_chamada = {
        chamada.get("id"): chamada.get("args")
        for mensagem in mensagens
        for chamada in (getattr(mensagem, "tool_calls", None) or [])
    }
    evidencias = []
    for mensagem in mensagens:
        if not isinstance(mensagem, ToolMessage):
            continue
        evidencia = {"tool": mensagem.name, "resultado": mensagem.content}
        argumentos = argumentos_por_chamada.get(mensagem.tool_call_id)
        if argumentos is not None:
            evidencia["argumentos"] = argumentos
        evidencias.append(evidencia)
    return evidencias


def _logar_passo_do_agente(mensagem: Any, inicio: float) -> None:
    chamadas = [chamada.get("name") for chamada in (getattr(mensagem, "tool_calls", None) or [])]
    # Retorno de tool traz dado pessoal (perfil, alergias): só o nome dela.
    if isinstance(mensagem, ToolMessage):
        previa = mensagem.name
    else:
        previa = chamadas or str(getattr(mensagem, "content", ""))[:_TAMANHO_PREVIA_LOG]
    logger.info("  [agente %.1fs] %s %s", time.perf_counter() - inicio, type(mensagem).__name__, previa)


async def _resposta_agente(agente: Any, entrada: str) -> tuple[str, list[dict[str, Any]]]:
    """Roda o agente e devolve `(texto, evidencias_tools)`.

    Sempre `await` no MESMO event loop em que o `pool` foi criado — nunca
    `asyncio.run()` aqui: um `asyncpg.Pool` fica preso ao loop de origem.
    O `content` pode vir como lista de blocos (Gemini), daí
    `extrair_texto_resposta`."""
    entrada_agente = {"messages": [("human", entrada)]}
    if hasattr(agente, "astream"):
        mensagens = await _executar_com_log_de_passos(agente, entrada_agente)
    else:
        mensagens = (await agente.ainvoke(entrada_agente))["messages"]
    return extrair_texto_resposta(mensagens[-1]), _extrair_evidencias_tools(mensagens)


async def _executar_com_log_de_passos(agente: Any, entrada_agente: dict[str, Any]) -> list[Any]:
    """Roda o agente passo a passo (`astream`), logando cada passo, e devolve
    as mensagens do último."""
    mensagens: list[Any] = []
    inicio = time.perf_counter()
    async for passo in agente.astream(
        entrada_agente, config={"recursion_limit": _LIMITE_PASSOS_AGENTE}, stream_mode="values"
    ):
        mensagens = passo["messages"]
        _logar_passo_do_agente(mensagens[-1], inicio)
    return mensagens


# --- leitura do JSON devolvido pelo especialista ---


_extrair_json = extrair_objeto_json


def _garantir_passos_da_rotina(resposta: dict, evidencias: list[dict] | None) -> dict:
    """Modelos pequenos dizem "sua rotina está pronta!" sem listar os passos. Se `suggest_routine`
    devolveu passos e a resposta não cita nenhum produto, anexa os passos REAIS da tool."""
    dados = dados_da_evidencia(evidencias, "suggest_routine")
    passos = dados.get("passos") if isinstance(dados, dict) else None
    if not passos:
        return resposta

    texto_da_resposta = f"{resposta.get('resposta', '')} {resposta.get('recomendacao', '')}".lower()
    if any(str(passo.get("nome", "")).lower() in texto_da_resposta for passo in passos):
        return resposta

    lista = "; ".join(f"{passo['ordem']}) {passo['nome']} ({passo['categoria']})" for passo in passos)
    resposta_atual = str(resposta.get("resposta", "")).strip()
    resposta["resposta"] = f"{resposta_atual} Passos ({dados.get('horario', '')}): {lista}.".strip()
    return resposta


def _resposta_de_falha(nome: str, intencao: str, texto: str) -> dict[str, Any]:
    """JSON no formato do especialista para quando ele não conseguiu responder."""
    return {
        "dominio": nome,
        "intencao": intencao,
        "resposta": texto,
        "recomendacao": "",
        "fontes_usadas": [],
    }


async def _executar_especialista(estado: EstadoVenus, nome: str, agente: Any) -> EstadoVenus:
    """Roda `agente` (já montado, com as tools do domínio) e grava o JSON
    devolvido em `resposta_especialista` (ou um JSON de erro, se a saída não
    for JSON válido)."""
    try:
        with usuario_da_conversa(estado.get("usuario_id_postgres")):
            texto, evidencias = await _resposta_agente(agente, _montar_entrada(estado))
    except Exception:
        # Falha de LLM/tool nunca derruba o grafo: vira JSON de erro técnico.
        logger.exception("Especialista %s falhou ao chamar o LLM/tools", nome)
        return {
            "resposta_especialista": _resposta_de_falha(nome, "erro_tecnico", _RESPOSTA_ESPECIALISTA_FALLBACK),
            "evidencias_tools": None,
        }

    try:
        resposta_json = _extrair_json(texto)
    except (TypeError, ValueError):
        logger.warning("Especialista %s não devolveu JSON válido (%d caracteres)", nome, len(texto or ""))
        resposta_json = _resposta_de_falha(nome, "erro_formato", _RESPOSTA_ERRO_FORMATO)

    if nome == "rotina":
        resposta_json = _garantir_passos_da_rotina(resposta_json, evidencias)

    return {"resposta_especialista": resposta_json, "evidencias_tools": evidencias}


# --- fábricas dos nós ---


def _prompt_com_data_atual(prompt: str) -> Callable[[dict[str, Any]], list[Any]]:
    """Prompt do agente ReAct com a data de cada chamada (o agente fica em cache)."""

    def _mensagens(estado_agente: dict[str, Any]) -> list[Any]:
        return [SystemMessage(content=com_data_atual(prompt)), *estado_agente["messages"]]

    return _mensagens


def _montar_no_especialista(
    nome: str, prompt: str, montar_tools: Callable[[], list[Any]]
) -> NoEspecialista:
    """Nó de especialista com o agente ReAct montado sob demanda.

    O agente só é montado (e as tools só exigem `pool`/`indice` de verdade)
    no primeiro uso real do nó, nunca na montagem do grafo; depois fica em
    cache, um por instância da fábrica."""
    agente: Any = None

    def _agente() -> Any:
        nonlocal agente
        if agente is None:
            # As tools primeiro: sem `pool`/`indice`, o erro que sobe é o delas.
            tools = montar_tools()
            agente = montar_agente_mcp(get_llm_especialista(), prompt=_prompt_com_data_atual(prompt), tools=tools)
        return agente

    async def no_especialista(estado: EstadoVenus) -> EstadoVenus:
        """Roda o agente e grava o JSON em `resposta_especialista`."""
        try:
            agente_pronto = _agente()
        except Exception:
            # Ex.: FAQ sem índice ou rotina sem pool. Antes, o ValueError saía do
            # nó e derrubava o grafo inteiro (502 na API para toda pergunta de
            # FAQ). Agora vira erro técnico, como uma falha de LLM; como o
            # agente não foi guardado, a próxima mensagem tenta montar de novo.
            logger.exception("Não consegui montar o agente %s", nome)
            return {
                "resposta_especialista": _resposta_de_falha(nome, "erro_tecnico", _RESPOSTA_ESPECIALISTA_FALLBACK),
                "evidencias_tools": None,
            }
        return await _executar_especialista(estado, nome, agente_pronto)

    return no_especialista


def montar_no_agente_produto(pool: Any) -> NoEspecialista:
    """Nó do agente de Produto (tools em `tools/produto.py`). O agente só é
    montado no primeiro uso; o nó é async, então o grafo roda via
    `.ainvoke()`/`.astream()`."""
    return _montar_no_especialista(
        "produto",
        ESP_PRODUTO_PROMPT_COMPLETO,
        lambda: montar_tools_produto(pool) + montar_tools_compartilhadas(pool),
    )


def montar_no_agente_ingrediente(pool: Any) -> NoEspecialista:
    """Idem `montar_no_agente_produto`, para o agente de Ingrediente (ver
    `tools/ingrediente.py`)."""
    return _montar_no_especialista(
        "ingrediente",
        ESP_INGREDIENTE_PROMPT_COMPLETO,
        lambda: montar_tools_ingrediente(pool) + montar_tools_compartilhadas(pool),
    )


def montar_no_agente_rotina(pool: Any, tools_extras: list[Any] | None = None) -> NoEspecialista:
    """Idem `montar_no_agente_produto`, para o agente de Rotina (ver
    `tools/rotina.py`) — perfil/favoritos/listas do usuário no Postgres,
    só leitura. A IA nunca altera os favoritos: pedido para adicionar ou
    remover recebe a recusa fixa `_RESPOSTA_ALTERAR_FAVORITO`, sem LLM.

    `tools_extras` recebe tools de SOMENTE LEITURA já montadas por quem
    monta o grafo — hoje, `check_availability`
    (`tools/calendario.py::montar_tools_calendario`), quando o Google
    Calendar está configurado. Sem nada aqui, o agente funciona como antes."""
    agente = _montar_no_especialista(
        "rotina",
        ROTINA_PROMPT_COMPLETO,
        lambda: montar_tools_rotina(pool) + montar_tools_compartilhadas(pool) + list(tools_extras or []),
    )

    async def no_agente_rotina(estado: EstadoVenus) -> EstadoVenus:
        pergunta = estado.get("pergunta_original") or estado.get("mensagem_usuario", "")
        resposta_ao_agendamento = await _responder_agendamento_pendente(estado, pool)
        if resposta_ao_agendamento is not None:
            return resposta_ao_agendamento
        if pede_alteracao_de_favorito(pergunta):
            return {
                "resposta_especialista": _resposta_de_falha(
                    "rotina", INTENCAO_NAO_SUPORTADA, _RESPOSTA_ALTERAR_FAVORITO
                ),
                "evidencias_tools": None,
            }
        saida = await agente(estado)
        # Proposta preparada agora fica guardada até a próxima mensagem.
        saida["agendamento_pendente"] = propostas_das_evidencias(saida.get("evidencias_tools")) or None
        return saida

    return no_agente_rotina


async def _responder_agendamento_pendente(estado: EstadoVenus, pool: Any) -> EstadoVenus | None:
    """Se há proposta de agendamento e a mensagem é "sim"/"não", resolve aqui,
    em código, sem LLM. `None` = a mensagem não é resposta a uma proposta."""
    pendentes = estado.get("agendamento_pendente")
    mensagem = estado.get("mensagem_anonimizada") or estado.get("mensagem_usuario", "")
    if not pendentes:
        return None
    if eh_confirmacao(mensagem):
        texto = await executar_propostas(pool, estado.get("usuario_id_postgres"), propostas_validas(pendentes))
    elif eh_negacao(mensagem):
        texto = RESPOSTA_CANCELADO
    else:
        return None
    return {
        "resposta_especialista": _resposta_de_falha("rotina", INTENCAO_AGENDAMENTO, texto),
        "evidencias_tools": None,
        "agendamento_pendente": None,
    }


def pede_alteracao_de_favorito(pergunta: str) -> bool:
    """True se a pergunta pede para adicionar ou remover um produto dos favoritos."""
    return pede_para_adicionar_favorito(pergunta) or pede_remocao_de_favorito(pergunta)


def pede_para_adicionar_favorito(pergunta: str) -> bool:
    """True se a pergunta pede para salvar um produto nos favoritos."""
    return bool(_ADICAO_DE_FAVORITO_RE.search(pergunta or ""))


def pede_remocao_de_favorito(pergunta: str) -> bool:
    """True se a pergunta pede para tirar um produto dos favoritos."""
    return bool(_REMOCAO_DE_FAVORITO_RE.search(pergunta or ""))


def montar_no_agente_faq(indice: Any, tools_extras: list[Any] | None = None) -> NoEspecialista:
    """Fábrica do nó do agente FAQ — o agente com RAG.

    `indice` é o índice do FAQ (`rag.criar_indice_faq`: Qdrant ou local); as
    tools são `faq_retriever` (documentos do FAQ) e `buscar_na_web` (internet).
    `tools_extras` recebe tools já carregadas de fontes externas — tools MCP
    (`mcp.tools.get_mcp_tools`) e/ou A2A (`a2a_client.montar_tool_a2a`).

    Devolve JSON com `fontes_usadas` e passa pelo Juiz, que confere a
    resposta contra os trechos recuperados.
    """
    return _montar_no_especialista(
        "faq",
        FAQ_PROMPT_COMPLETO,
        lambda: montar_tools_faq(indice) + list(tools_extras or []),
    )
