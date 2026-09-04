"""Estado compartilhado do grafo principal do Venus."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

Rota = Literal["produto", "ingrediente", "rotina", "faq"]


class EstadoVenus(TypedDict, total=False):
    """Estado compartilhado entre todos os nós do `StateGraph` principal.

    TODO: revisar/ajustar os campos conforme a implementação de cada nó
    (`nodes/`) avançar — este é o desenho inicial baseado no fluxo descrito
    nos prompts (roteador -> especialista -> agente juiz -> orquestrador).
    """

    # --- entrada ---
    # Identificador estável do usuário (distinto do thread_id de conversa) —
    # chave da memória de longo prazo em `memory/store.py`. Sem ele, os nós
    # de `nodes/memoria.py` não leem nem gravam nada (conversa segue sem
    # memória de longo prazo, como antes desse campo existir).
    usuario_id: str | None
    mensagem_usuario: str
    # reducer add_messages: os nós devolvem só a(s) mensagem(ns) nova(s) (ver
    # `nodes/guardrails.py`) — o LangGraph acumula no histórico existente em
    # vez de sobrescrever. Persiste entre chamadas quando o grafo é compilado
    # com um checkpointer (ver `memory/checkpointer.py`) e o mesmo
    # `thread_id` é usado a cada `invoke`.
    historico: Annotated[list[BaseMessage], add_messages]

    # --- guardrail de entrada ---
    entrada_bloqueada: bool
    motivo_bloqueio: str | None
    mensagem_anonimizada: str | None

    # --- memória de longo prazo (ver nodes/memoria.py) ---
    # Perfil carregado do `store` no início do turno (None se o usuário é
    # novo ou não há `store`/`usuario_id`); pode ser atualizado no fim do
    # turno com fatos novos extraídos desta troca.
    memorias_usuario: dict[str, Any] | None

    # --- roteador ---
    rota: Rota | None
    pergunta_original: str

    # --- especialista (produto | ingrediente | rotina | faq) ---
    resposta_especialista: dict[str, Any] | None

    # --- agente juiz ---
    tentativas_juiz: int
    aprovado_juiz: bool | None
    feedback_juiz: str | None

    # --- orquestrador ---
    resposta_final: str | None

    # --- guardrail de saída ---
    saida_bloqueada: bool
