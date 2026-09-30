"""Nós dos agentes especialistas: produto, ingrediente, rotina e FAQ (RAG)."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import ToolMessage

from venus_sdk.flows.agente_mcp import montar_agente_mcp
from venus_sdk.llm.models import extrair_texto_resposta, get_llm_especialista
from venus_sdk.nodes._evidencias import dados_da_evidencia
from venus_sdk.prompts.faq import FAQ_PROMPT_COMPLETO
from venus_sdk.prompts.ingrediente import ESP_INGREDIENTE_PROMPT_COMPLETO
from venus_sdk.prompts.produto import ESP_PRODUTO_PROMPT_COMPLETO
from venus_sdk.prompts.rotina import ROTINA_PROMPT_COMPLETO
from venus_sdk.state import EstadoVenus
from venus_sdk.texto import remover_acentos
from venus_sdk.tools.compartilhadas import montar_tools_compartilhadas
from venus_sdk.tools.faq import montar_tools_faq
from venus_sdk.tools.ingrediente import montar_tools_ingrediente
from venus_sdk.tools.produto import montar_tools_produto
from venus_sdk.tools.rotina import montar_tools_rotina

logger = logging.getLogger(__name__)

NoEspecialista = Callable[[EstadoVenus], Awaitable[EstadoVenus]]

# Fallback pra quando o especialista falha por completo (ex.: Gemini E Groq
# indisponíveis ao mesmo tempo — ver nota em `_resposta_agente`) — evita que
# um erro de provedor de LLM derrube a conversa inteira sem resposta
# nenhuma; o Agente Juiz reprova isto naturalmente (fontes_usadas vazio),
# então depois de `MAX_TENTATIVAS_JUIZ` o orquestrador ainda comunica o
# problema com transparência (ver `nodes/juiz.py`/`nodes/orquestrador.py`).
_RESPOSTA_ESPECIALISTA_FALLBACK = (
    "Tive um probleminha pra buscar essas informações agora. Pode tentar "
    "de novo em instantes?"
)
_RESPOSTA_ERRO_FORMATO = "Não consegui estruturar uma resposta válida para essa pergunta."

# Teto de passos do agente ReAct por pergunta; cada rodada de tool gasta 2
# (LLM + tool). Com 14, o agente de ingrediente que consulta as 5 tools uma a
# uma estourava o teto e devolvia "Sorry, need more steps to process this
# request." no lugar do JSON (teste de 2026-09-26).
_LIMITE_PASSOS_AGENTE = 24
_TAMANHO_PREVIA_LOG = 80

_FAVORITO_RE = re.compile(r"favorit", re.IGNORECASE)
_VERBO_DE_ADICAO_RE = re.compile(
    r"\b(adicion\w*|inclu\w*|coloc\w*|salv\w*|guard\w*|bot[ae]\w*|p[oõ]e|marc[ae]\w*)\b", re.IGNORECASE
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

_CERCA_MARKDOWN_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


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


# --- execução do agente ReAct ---


def _extrair_evidencias_tools(mensagens: list) -> list[dict[str, Any]]:
    """Extrai nome+retorno de cada `ToolMessage` da execução do agente ReAct
    — a evidência bruta que embasa (ou não) `resposta_especialista`, usada
    pelo Agente Juiz pra cruzar contra `fontes_usadas` (ver
    `nodes/juiz.py`). Sem isto, o Juiz só via o JSON final do especialista e
    não tinha como perceber quando uma tool citada não sustentava, de
    verdade, a afirmação feita (achado de um teste de conversa real em
    2026-09-10 — ver `EstadoVenus.evidencias_tools`).

    Inclui `argumentos` (os parâmetros da chamada, ex.: `ingredient_id`)
    quando a chamada correspondente aparece nas mensagens: sem isso não dá
    pra saber de QUAL item é cada retorno quando o agente consulta mais de um
    (teste de 2026-09-26: detalhes de um ingrediente apareceram na resposta
    sobre outro)."""
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
    previa = chamadas or str(getattr(mensagem, "content", ""))[:_TAMANHO_PREVIA_LOG]
    logger.info("  [agente %.1fs] %s %s", time.perf_counter() - inicio, type(mensagem).__name__, previa)


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


def _normalizar_chaves(dados: Any) -> Any:
    """Modelos menores escrevem "domínio"/"intenção" (com acento): normaliza as chaves do
    objeto de topo para o contrato (`dominio`, `intencao`...)."""
    if isinstance(dados, dict):
        return {remover_acentos(str(chave)): valor for chave, valor in dados.items()}
    return dados


def _candidatos_a_json(texto: str) -> list[str]:
    """O texto como veio, sem a cerca ```json``` e só o trecho entre a 1ª `{` e a última `}`."""
    bruto = (texto or "").strip()
    candidatos = [bruto, _CERCA_MARKDOWN_RE.sub("", bruto).strip()]
    inicio, fim = bruto.find("{"), bruto.rfind("}")
    if inicio != -1 and fim > inicio:
        candidatos.append(bruto[inicio : fim + 1])
    return candidatos


def _extrair_json(texto: str) -> Any:
    """Faz `json.loads` tolerando o que os LLMs costumam fazer: cercar o JSON com
    ```json ... ```, colocar uma frase antes/depois, quebrar linha DENTRO de uma string
    (JSON inválido no modo estrito) e acentuar nomes de campo. Levanta
    `ValueError`/`TypeError` se não houver um objeto JSON válido."""
    for candidato in _candidatos_a_json(texto):
        try:
            return _normalizar_chaves(json.loads(candidato, strict=False))
        except ValueError:
            continue
    raise ValueError("nenhum objeto JSON na resposta do especialista")


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
        texto, evidencias = await _resposta_agente(agente, _montar_entrada(estado))
    except Exception:
        # Gemini E o fallback Groq falharam (ou algo mais quebrou dentro do
        # agente ReAct) — nunca deixa isso subir cru até `.ainvoke()` do
        # grafo principal (ver `_RESPOSTA_ESPECIALISTA_FALLBACK`); vira um
        # JSON reprovável normalmente pelo Agente Juiz, não um crash.
        logger.exception("Especialista %s falhou ao chamar o LLM/tools", nome)
        return {
            "resposta_especialista": _resposta_de_falha(nome, "erro_tecnico", _RESPOSTA_ESPECIALISTA_FALLBACK),
            "evidencias_tools": None,
        }

    try:
        resposta_json = _extrair_json(texto)
    except (TypeError, ValueError):
        logger.warning("Especialista %s não devolveu JSON válido: %r", nome, texto)
        resposta_json = _resposta_de_falha(nome, "erro_formato", _RESPOSTA_ERRO_FORMATO)

    if nome == "rotina" and isinstance(resposta_json, dict):
        resposta_json = _garantir_passos_da_rotina(resposta_json, evidencias)

    return {"resposta_especialista": resposta_json, "evidencias_tools": evidencias}


# --- fábricas dos nós ---


def _montar_no_especialista(
    nome: str, prompt: str, montar_tools: Callable[[], list[Any]]
) -> NoEspecialista:
    """Nó de especialista com o agente ReAct montado sob demanda.

    O agente só é montado (e as tools só exigem `pool`/`indice` de verdade)
    no primeiro uso real do nó, nunca na montagem do grafo; depois fica em
    cache, um por instância da fábrica."""
    cache: dict[str, Any] = {}

    def _agente() -> Any:
        if nome not in cache:
            tools = montar_tools()
            cache[nome] = montar_agente_mcp(get_llm_especialista(), prompt=prompt, tools=tools)
        return cache[nome]

    async def no_especialista(estado: EstadoVenus) -> EstadoVenus:
        """Roda o agente e grava o JSON em `resposta_especialista`."""
        return await _executar_especialista(estado, nome, _agente())

    return no_especialista


def montar_no_agente_produto(pool: Any) -> NoEspecialista:
    """Fábrica do nó do agente de Produto — recebe o `pool` do Postgres (ver
    `tools/produto.py`) e devolve o nó pronto pra registrar no grafo
    (`flows/venus_flow.py::montar_grafo_venus`).

    O agente ReAct só é montado (e as tools só exigem `pool` de verdade) no
    primeiro uso real do nó, nunca na montagem do grafo (ver
    `_montar_no_especialista`).

    O nó é `async def` (ver `_resposta_agente`) — o grafo precisa ser
    invocado via `.ainvoke()`/`.astream()`, nunca `.invoke()`, quando `pool`
    não for `None` (ver `examples/conversar_com_venus.py`).
    """
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
        if pede_alteracao_de_favorito(pergunta):
            return {
                "resposta_especialista": _resposta_de_falha(
                    "rotina", INTENCAO_NAO_SUPORTADA, _RESPOSTA_ALTERAR_FAVORITO
                ),
                "evidencias_tools": None,
            }
        return await agente(estado)

    return no_agente_rotina


def pede_alteracao_de_favorito(pergunta: str) -> bool:
    """True se a pergunta pede para adicionar ou remover um produto dos favoritos."""
    return pede_para_adicionar_favorito(pergunta) or pede_remocao_de_favorito(pergunta)


def pede_para_adicionar_favorito(pergunta: str) -> bool:
    """True se a pergunta pede para salvar um produto nos favoritos."""
    texto = pergunta or ""
    return bool(_FAVORITO_RE.search(texto) and _VERBO_DE_ADICAO_RE.search(texto))


def pede_remocao_de_favorito(pergunta: str) -> bool:
    """True se a pergunta pede para tirar um produto dos favoritos."""
    return bool(_REMOCAO_DE_FAVORITO_RE.search(pergunta or ""))


def montar_no_agente_faq(indice: Any, tools_extras: list[Any] | None = None) -> NoEspecialista:
    """Fábrica do nó do agente FAQ — o agente com RAG.

    `indice` é o índice do FAQ (`rag.criar_indice_faq`: Qdrant ou local); as
    tools são `faq_retriever` (documentos do FAQ) e `buscar_na_web` (internet).
    `tools_extras` recebe tools já carregadas de fontes externas — tools MCP
    (`mcp.tools.get_mcp_tools`) e/ou A2A (`a2a_client.montar_tool_a2a`).

    Diferente do desenho antigo, o FAQ agora devolve JSON estruturado (com
    `fontes_usadas` = arquivos/URLs realmente consultados) e passa pelo Agente
    Juiz, que confere a resposta contra os trechos recuperados
    (`evidencias_tools`) — mitigação de alucinação também no RAG.
    """
    return _montar_no_especialista(
        "faq",
        FAQ_PROMPT_COMPLETO,
        lambda: montar_tools_faq(indice) + list(tools_extras or []),
    )
