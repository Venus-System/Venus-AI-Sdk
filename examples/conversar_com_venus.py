"""Script de teste manual do grafo do Venus — conversa via terminal.

Diferente de `examples/basic_usage.py` (que só monta e inspeciona a
topologia), este script COMPILA e RODA o grafo de verdade: chama os LLMs
configurados (Gemini/Groq) a cada mensagem. Serve pra testar o fluxo
guardrail -> roteador -> especialista -> juiz -> orquestrador -> guardrail
manualmente pelo terminal (ou dando F5 nele), sem escrever um teste
automatizado toda vez.

Requisitos:
    - GEMINI_API_KEY e GROQ_API_KEY configuradas no `.env` da raiz do projeto.
    - DATABASE_URL configurada, pro pool do Postgres (`asyncpg`) que os
      agentes de produto/ingrediente usam (ver `tools/produto.py`,
      `tools/ingrediente.py`) — sem ela, esses dois especialistas levantam
      `ValueError` quando o roteador cair neles (small talk, rotina e FAQ não
      precisam dela).
    - Rotina/FAQ ainda levantam NotImplementedError até `mcp/tools.py` ser
      implementado de verdade (ver `docs/architecture.md`).

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

import asyncpg

from venus_sdk.config.settings import DATABASE_URL, validar_config
from venus_sdk.flows.venus_flow import compilar_grafo_venus
from venus_sdk.memory import criar_checkpointer_mongo

# thread_id fixo (em vez de um uuid novo a cada execução): assim dá pra
# fechar o script e rodar de novo que a conversa continua de onde parou —
# prova de que o checkpointer Mongo sobrevive a um restart do processo
# (o `InMemorySaver` não sobreviveria). Pra começar uma conversa do zero,
# troque este valor (ou apague o banco `venus` no Mongo).
THREAD_ID_LOCAL = "conversa-local"


async def main() -> None:
    problemas = validar_config()
    if problemas:
        print("Configuração incompleta — corrija o .env antes de continuar:")
        for problema in problemas:
            print(f"  - {problema}")
        return

    # checkpointer em Mongo + thread_id fixo: o grafo lembra sozinho do
    # histórico entre as mensagens desta conversa (ver
    # `memory/checkpointer.py`) — e, diferente do checkpointer em memória,
    # o histórico persiste no MongoDB (`MONGODB_URL` no `.env`) mesmo se
    # você fechar e abrir o script de novo.
    try:
        checkpointer = criar_checkpointer_mongo()
    except (ImportError, ValueError) as erro:
        print(f"Não foi possível criar o checkpointer Mongo: {erro}")
        return

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
            print(f"Não foi possível criar o pool do Postgres: {erro}")
            print("Seguindo sem ele — produto/ingrediente vão falhar até isso ser corrigido.\n")
    else:
        print("DATABASE_URL não configurada — produto/ingrediente vão falhar até isso ser corrigido.\n")

    grafo = compilar_grafo_venus(checkpointer=checkpointer, pool=pool)
    config = {"configurable": {"thread_id": THREAD_ID_LOCAL}}
    print("Venus (teste manual) — digite 'sair' para encerrar.\n")

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
                estado = await grafo.ainvoke({"mensagem_usuario": mensagem}, config=config)
            except NotImplementedError as erro:
                print(f"[ainda não implementado] {erro}\n")
                continue
            except Exception as erro:  # noqa: BLE001 - script de teste manual: qualquer erro deve aparecer
                print(f"[erro ao rodar o grafo] {erro}\n")
                continue

            resposta = estado.get("resposta_final") or "(sem resposta_final no estado)"
            print(f"Venus: {resposta}\n")
    finally:
        if pool is not None:
            await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
