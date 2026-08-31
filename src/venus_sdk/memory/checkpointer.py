"""Checkpointer do grafo principal — persistência de contexto/histórico
entre execuções, por `thread_id` (uma conversa).

Hoje só em memória (`InMemorySaver`): dura enquanto o processo estiver de
pé, e some ao reiniciar. Para persistência entre reinícios (SQLite,
Postgres, Redis...), trocar a implementação aqui por um checkpointer
persistente (ex.: `langgraph-checkpoint-sqlite`) — o resto do SDK não
precisa mudar, já que só depende da interface `BaseCheckpointSaver` que
`compilar_grafo_venus(checkpointer=...)` espera.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver


def criar_checkpointer_em_memoria() -> InMemorySaver:
    """Cria um checkpointer novo, em memória (RAM).

    Cada chamada devolve uma instância própria — não compartilhe uma mesma
    instância entre processos/threads que não devam ver a memória um do
    outro (ex.: testes).
    """
    return InMemorySaver()
