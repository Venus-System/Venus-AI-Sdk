"""Script de teste manual do grafo do Venus — conversa via terminal.

Diferente de `examples/basic_usage.py` (que só monta e inspeciona a
topologia), este script COMPILA e RODA o grafo de verdade: chama os LLMs
configurados (Gemini/Groq) a cada mensagem. Serve pra testar o fluxo
guardrail -> roteador -> especialista -> juiz -> orquestrador -> guardrail
manualmente pelo terminal (ou dando F5 nele), sem escrever um teste
automatizado toda vez.

Requisitos:
    - GEMINI_API_KEY e GROQ_API_KEY configuradas no `.env` da raiz do projeto.
    - Os nós que dependem do client MCP (produto/ingrediente/rotina/faq)
      ainda levantam NotImplementedError até `mcp/tools.py` ser implementado
      (ver `docs/architecture.md`) — small talk e mensagens bloqueadas pelo
      guardrail de entrada já funcionam ponta a ponta.

Uso:
    python examples/conversar_com_venus.py
"""

from __future__ import annotations

import uuid

from venus_sdk.config.settings import validar_config
from venus_sdk.flows.venus_flow import compilar_grafo_venus
from venus_sdk.memory import criar_checkpointer_em_memoria


def main() -> None:
    problemas = validar_config()
    if problemas:
        print("Configuração incompleta — corrija o .env antes de continuar:")
        for problema in problemas:
            print(f"  - {problema}")
        return

    # checkpointer em memória + thread_id fixo por execução: o grafo lembra
    # sozinho do histórico entre as mensagens desta conversa (ver
    # `memory/checkpointer.py`) — não precisamos mais montar a lista à mão.
    grafo = compilar_grafo_venus(checkpointer=criar_checkpointer_em_memoria())
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    print("Venus (teste manual) — digite 'sair' para encerrar.\n")

    while True:
        try:
            mensagem = input("Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if mensagem.lower() in {"sair", "exit", "quit"}:
            break
        if not mensagem:
            continue

        try:
            estado = grafo.invoke({"mensagem_usuario": mensagem}, config=config)
        except NotImplementedError as erro:
            print(f"[ainda não implementado] {erro}\n")
            continue
        except Exception as erro:  # noqa: BLE001 - script de teste manual: qualquer erro deve aparecer
            print(f"[erro ao rodar o grafo] {erro}\n")
            continue

        resposta = estado.get("resposta_final") or "(sem resposta_final no estado)"
        print(f"Venus: {resposta}\n")


if __name__ == "__main__":
    main()
