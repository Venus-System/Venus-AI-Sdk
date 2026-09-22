"""Script de teste manual do grafo do Venus — conversa via terminal.

Diferente de `examples/basic_usage.py` (que só monta e inspeciona a
topologia), este script COMPILA e RODA o grafo de verdade: chama os LLMs
configurados (Gemini/Groq) a cada mensagem. Serve pra testar o fluxo
guardrail -> roteador -> especialista -> juiz -> orquestrador -> guardrail
manualmente pelo terminal (ou dando F5 nele), sem escrever um teste
automatizado toda vez.

Requisitos:
    - GEMINI_API_KEY e GROQ_API_KEY configuradas no `.env`.
    - Postgres com schema+seed: `docker compose up -d && python scripts/init_db.py`
      e `DATABASE_URL` no `.env` (produto/ingrediente/rotina).
    - RAG do FAQ: documentos em `data/faq/` (já incluídos) — índice local, sem
      dependência externa; a busca na web usa Tavily (`TAVILY_API_KEY`) ou
      DuckDuckGo.
    - Opcionais: `VENUS_USE_MCP=1` (FAQ passa a também usar as tools do
      servidor MCP do Venus, em subprocesso), `A2A_AGENTES_EXTERNOS`
      (agente externo via A2A), `MONGODB_URI` (checkpointer persistente;
      sem ele usa memória em RAM), `VENUS_USER_ID` (user_id do Postgres —
      1 ou 2 no seed) e `VENUS_USUARIO` (chave da memória de longo prazo).

O script inteiro roda dentro de um ÚNICO event loop (`asyncio.run(main())`)
— o grafo é invocado via `.ainvoke()`, nunca `.invoke()`. Isso é obrigatório
quando há `pool`: um `asyncpg.Pool` fica preso ao loop onde foi criado, e os
nós de produto/ingrediente são `async def` (ver
`nodes/especialistas.py::montar_no_agente_produto`) — chamá-los via
`asyncio.run()` a cada mensagem (como uma versão antiga deste script fazia)
cria e fecha um loop novo por chamada, o que derruba o pool com
`RuntimeError: Event loop is closed` já na segunda mensagem.

Uso:
    python examples/conversar_com_venus.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import warnings

import asyncpg
from langchain_core.callbacks import AsyncCallbackHandler

from venus_sdk.config.settings import DATABASE_URL, validar_config
from venus_sdk.flows.venus_flow import compilar_grafo_venus
from venus_sdk.a2a_client import montar_tool_a2a
from venus_sdk.config.settings import FAQ_DIR, MONGODB_URI
from venus_sdk.mcp.tools import get_mcp_tools
from venus_sdk.memory import (
    criar_checkpointer_em_memoria,
    criar_checkpointer_mongo,
    criar_store_em_memoria,
)
from venus_sdk.rag import criar_indice_local

# thread_id fixo (em vez de um uuid novo a cada execução): assim dá pra
# fechar o script e rodar de novo que a conversa continua de onde parou —
# prova de que o checkpointer Mongo sobrevive a um restart do processo
# (o `InMemorySaver` não sobreviveria). Pra começar uma conversa do zero,
# troque este valor (ou apague o banco `venus` no Mongo).
THREAD_ID_LOCAL = "conversa-local"
# Por padrão o chat só mostra a conversa (sem logs, rastros de nós/LLM/tools nem erros técnicos).
# VENUS_DEBUG=1 liga tudo isso de volta pra depurar.
DEBUG = os.getenv("VENUS_DEBUG") == "1"
_MSG_ERRO = "Tive um probleminha agora. Pode tentar de novo em instantes?"
_MSG_DEMORA = "Estou demorando mais que o normal pra responder. Pode tentar de novo em instantes?"
TIMEOUT_TURNO = 120  # segundos por mensagem


class _Rastreio(AsyncCallbackHandler):
    """Imprime cada chamada de LLM e de tool (com tempo e erro) enquanto o
    especialista trabalha — mostra ONDE trava (Gemini, Groq ou banco)."""

    def __init__(self) -> None:
        self._t: dict = {}

    def _ini(self, run_id) -> None:
        self._t[run_id] = time.perf_counter()

    def _dur(self, run_id) -> float:
        return time.perf_counter() - self._t.pop(run_id, time.perf_counter())

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kw) -> None:
        self._ini(run_id)
        nome = (serialized or {}).get("name") or (serialized or {}).get("id", ["LLM"])[-1]
        print(f"      → LLM {nome} chamado...", flush=True)

    async def on_llm_end(self, response, *, run_id, **kw) -> None:
        print(f"      ← LLM respondeu ({self._dur(run_id):.1f}s)", flush=True)

    async def on_llm_error(self, error, *, run_id, **kw) -> None:
        print(f"      ✗ LLM ERRO ({self._dur(run_id):.1f}s): {type(error).__name__}: {str(error)[:300]}", flush=True)

    async def on_tool_start(self, serialized, input_str, *, run_id, **kw) -> None:
        self._ini(run_id)
        print(f"      → tool {(serialized or {}).get('name')}({str(input_str)[:120]})", flush=True)

    async def on_tool_end(self, output, *, run_id, **kw) -> None:
        print(f"      ← tool ok ({self._dur(run_id):.1f}s): {str(getattr(output, 'content', output))[:160]}", flush=True)

    async def on_tool_error(self, error, *, run_id, **kw) -> None:
        print(f"      ✗ tool ERRO ({self._dur(run_id):.1f}s): {type(error).__name__}: {error}", flush=True)


async def _preflight(pool) -> None:
    """Testa banco, Gemini e Groq antes da conversa — cada um com timeout —
    para separar 'a chave/cota/rede não funciona' de 'o código travou'."""
    async def testar(nome: str, coro) -> None:
        t0 = time.perf_counter()
        try:
            await asyncio.wait_for(coro, 20)
            print(f"  [OK   ] {nome} ({time.perf_counter() - t0:.1f}s)", flush=True)
        except asyncio.TimeoutError:
            print(f"  [FALHA] {nome}: sem resposta em 20s", flush=True)
        except Exception as erro:  # noqa: BLE001
            print(f"  [FALHA] {nome}: {type(erro).__name__}: {str(erro)[:250]}", flush=True)

    print("Verificando conexões...", flush=True)
    if pool is not None:
        async def _db():
            async with pool.acquire() as c:
                n = await c.fetchval("SELECT count(*) FROM venus.products")
                print(f"        (venus.products tem {n} linhas)", flush=True)
        await testar("Postgres", _db())
    from venus_sdk.llm.models import _criar_modelo, cadeia_especialista, cadeia_rapida

    for titulo, cadeia, rapido in (("especialistas", cadeia_especialista(), False), ("roteador/juiz", cadeia_rapida(), True)):
        print(f"  Cadeia de LLMs ({titulo}): " + " -> ".join(f"{p}:{m}" for p, m in cadeia), flush=True)
        for p, m in cadeia:
            await testar(f"{p}:{m}", _criar_modelo(p, m, rapido=rapido).ainvoke("responda só: ok"))
    print(flush=True)


async def _rodar_turno(grafo, mensagem: str, identidade: dict, config: dict) -> str | None:
    """Roda um turno mostrando cada nó do grafo (com tempo) e as tools
    chamadas — assim, se algo travar ou falhar, dá pra ver ONDE."""
    resposta = None
    t0 = time.perf_counter()
    if DEBUG:
        print("  · (processando...)", flush=True)
    async for atualizacao in grafo.astream(
        {"mensagem_usuario": mensagem, **identidade}, config=config, stream_mode="updates"
    ):
        for no, dados in atualizacao.items():
            dados = dados or {}
            extra = ""
            if dados.get("rota"):
                extra += f" rota={dados['rota']}"
            if dados.get("evidencias_tools"):
                extra += " tools=" + ",".join(e["tool"] for e in dados["evidencias_tools"])
            if dados.get("aprovado_juiz") is not None:
                extra += f" aprovado={dados['aprovado_juiz']}"
                if dados.get("feedback_juiz"):
                    extra += f" feedback={dados['feedback_juiz'][:120]!r}"
            if dados.get("resposta_especialista"):
                r = dados["resposta_especialista"]
                extra += f" intencao={r.get('intencao')} fontes={r.get('fontes_usadas')}"
            if DEBUG:
                print(f"  · {no} ({time.perf_counter() - t0:.1f}s){extra}", flush=True)
            if dados.get("resposta_final"):
                resposta = dados["resposta_final"]
    return resposta


async def main() -> None:
    # stdout sem buffer: no VS Code/F5 a saída é um pipe e os prints só
    # apareciam depois, parecendo que o programa tinha travado.
    sys.stdout.reconfigure(line_buffering=True)
    if DEBUG:
        logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(name)s: %(message)s")
        logging.getLogger("venus_sdk").setLevel(logging.INFO)
    else:
        logging.disable(logging.CRITICAL)  # nada de log técnico misturado na conversa
        warnings.filterwarnings("ignore")
    problemas = validar_config(exigir_banco=False)
    if problemas:
        print("Configuração incompleta — corrija o .env antes de continuar:")
        for problema in problemas:
            print(f"  - {problema}")
        return

    # Checkpointer (histórico por thread_id): Mongo se houver MONGODB_URI,
    # senão RAM. Store = memória de LONGO PRAZO por usuario_id.
    if MONGODB_URI:
        try:
            checkpointer = criar_checkpointer_mongo()
        except (ImportError, ValueError) as erro:
            print(f"Não foi possível criar o checkpointer Mongo: {erro}")
            return
    else:
        checkpointer = criar_checkpointer_em_memoria()
    store = criar_store_em_memoria()

    # Pool do Postgres pras tools de produto/ingrediente (ver
    # `tools/produto.py`, `tools/ingrediente.py`) — o SDK nunca cria essa
    # conexão sozinho, quem monta o grafo cria e gerencia (mesmo espírito do
    # checkpointer acima). Sem `DATABASE_URL`, seguimos com `pool=None`: só
    # produto/ingrediente ficam indisponíveis, o resto do grafo funciona.
    pool: asyncpg.Pool | None = None
    if DATABASE_URL:
        try:
            # min_size/max_size baixos de propósito: este Postgres de teste
            # tem `max_connections=20` no total (visto em 2026-09-08), boa
            # parte já ocupada por conexões internas do próprio serviço —
            # o padrão do asyncpg (min_size=10, max_size=10) sozinho quase
            # esgota isso, e sobra "sorry, too many clients already" pra
            # qualquer outra conexão (inclusive uma segunda instância deste
            # script rodando ao mesmo tempo). Script de teste manual não
            # tem tráfego concorrente de verdade — 3 conexões sobram.
            pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
        except Exception as erro:  # noqa: BLE001 - qualquer erro de conexão deve aparecer
            if DEBUG:
                print(f"Não foi possível criar o pool do Postgres: {erro}")
            print("Não consegui acessar o banco de produtos agora; vou responder só o que der sem ele.\n")
    elif DEBUG:
        print("DATABASE_URL não configurada — produto/ingrediente vão falhar até isso ser corrigido.\n")

    # RAG: índice local sobre data/faq (fonte externa 1) + web (fonte 2);
    # tools extras: MCP e/ou A2A (fontes 3 e 4), entregues ao agente FAQ.
    indice = criar_indice_local(FAQ_DIR, cache=".venus_cache/faq_index.npz")
    extras = list(montar_tool_a2a())
    if os.getenv("VENUS_USE_MCP") == "1":
        extras += await get_mcp_tools(apenas={"buscar_na_web"})

    grafo = compilar_grafo_venus(
        checkpointer=checkpointer, store=store, pool=pool, indice_rag=indice, tools_faq_extras=extras
    )
    config = {"configurable": {"thread_id": THREAD_ID_LOCAL}, "callbacks": [_Rastreio()] if DEBUG else []}
    identidade = {"usuario_id": os.getenv("VENUS_USUARIO", "usuario-demo")}
    if os.getenv("VENUS_USER_ID"):
        identidade["usuario_id_postgres"] = int(os.environ["VENUS_USER_ID"])
    if DEBUG:
        await _preflight(pool)
    print("Oi, eu sou a Venus! Pergunte sobre produtos, ingredientes ou rotina (digite 'sair' para encerrar).\n")

    try:
        while True:
            try:
                # `input()` bloqueia a thread, mas não tem nada mais rodando
                # nesse loop enquanto o usuário digita — sem problema aqui.
                mensagem = input("Você: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if mensagem.lower() in {"sair", "exit", "quit"}:
                break
            if not mensagem:
                continue

            try:
                resposta = await asyncio.wait_for(_rodar_turno(grafo, mensagem, identidade, config), TIMEOUT_TURNO)
            except asyncio.TimeoutError:
                print(f"Venus: {_MSG_DEMORA}\n", flush=True)
                continue
            except Exception as erro:  # noqa: BLE001 - o usuário nunca vê o erro técnico
                if DEBUG:
                    logging.getLogger("venus").exception("erro ao rodar o grafo")
                    print(f"[erro ao rodar o grafo] {type(erro).__name__}: {erro}")
                print(f"Venus: {_MSG_ERRO}\n", flush=True)
                continue

            print(f"Venus: {resposta or _MSG_ERRO}\n", flush=True)
    finally:
        if pool is not None:
            await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
