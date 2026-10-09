"""Estado compartilhado do grafo principal do Venus."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

Rota = Literal["produto", "ingrediente", "rotina", "faq"]
# Cara da Venus que acompanha a resposta (a web mostra a Veninha com ela).
# Lista fechada, escolhida só pelo código: `magoada` é a reação a ofensa (o
# roteador marca com `REACAO=magoada`, ver `nodes/roteador.py`); o resto é
# `neutra`.
Expressao = Literal["neutra", "magoada"]


class EstadoVenus(TypedDict, total=False):
    """Estado compartilhado entre todos os nós do `StateGraph` principal
    (guardrail -> memória -> roteador -> especialista -> juiz -> orquestrador
    -> guardrail -> memória)."""

    # --- entrada ---
    # Chave da memória de longo prazo (`memory/store.py`); sem ela, os nós de
    # `nodes/memoria.py` não leem nem gravam nada.
    usuario_id: str | None
    # `venus.users.user_id` — distinto de `usuario_id`. Só com ele os
    # especialistas recebem `USER_ID_POSTGRES=` e podem chamar as tools que
    # exigem `user_id`; sem ele, devem admitir que não sabem, nunca inventar.
    usuario_id_postgres: int | None
    mensagem_usuario: str
    # Reducer `add_messages`: cada nó devolve só as mensagens novas e o
    # LangGraph acumula (persistido pelo checkpointer, por `thread_id`).
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
    # Zerada para `neutra` no guardrail de entrada a cada turno; só o roteador
    # põe `magoada`, e o guardrail de saída volta para `neutra` se trocar o texto.
    expressao: Expressao

    # --- especialista (produto | ingrediente | rotina | faq) ---
    resposta_especialista: dict[str, Any] | None
    # Retorno bruto de cada tool chamada nesta tentativa: é contra isso que o
    # Juiz confere as afirmações do especialista (não só a coerência do JSON).
    evidencias_tools: list[dict[str, Any]] | None

    # --- agente juiz ---
    tentativas_juiz: int
    aprovado_juiz: bool | None
    feedback_juiz: str | None

    # --- orquestrador ---
    resposta_final: str | None

    # --- agendamento na agenda Google (ver nodes/agendamento.py) ---
    # Propostas preparadas neste turno e ainda não confirmadas. Só a próxima
    # mensagem do usuário pode confirmá-las ("sim"); qualquer outra descarta.
    agendamento_pendente: list[dict[str, Any]] | None

    # --- guardrail de saída ---
    saida_bloqueada: bool
