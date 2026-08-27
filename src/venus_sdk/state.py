"""Estado compartilhado do grafo principal do Venus."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from langchain_core.messages import BaseMessage

Rota = Literal["produto", "ingrediente", "rotina", "faq"]


class EstadoVenus(TypedDict, total=False):
    """Estado compartilhado entre todos os nós do `StateGraph` principal.

    TODO: revisar/ajustar os campos conforme a implementação de cada nó
    (`nodes/`) avançar — este é o desenho inicial baseado no fluxo descrito
    nos prompts (roteador -> especialista -> agente juiz -> orquestrador).
    """

    # --- entrada ---
    mensagem_usuario: str
    historico: list[BaseMessage]

    # --- guardrail de entrada ---
    entrada_bloqueada: bool
    motivo_bloqueio: str | None
    mensagem_anonimizada: str | None

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
