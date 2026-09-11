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
    # ID inteiro do usuário no Postgres (`venus.users.user_id`) — DISTINTO de
    # `usuario_id` acima (chave string da memória de longo prazo). Tools que
    # exigem `user_id` (`get_user_allergies`, `get_personalized_score`, ver
    # `tools/compartilhadas.py`/`tools/produto.py`) precisam dele; sem este
    # campo, os especialistas não têm como saber o `user_id` real e não devem
    # chamar essas tools (ver `USER_ID_POSTGRES=` em
    # `nodes/especialistas.py::_montar_entrada` e a nota compartilhada em
    # `prompts/comum.py::IDENTIFICADOR_USUARIO_NOTA`) — achado de um teste de
    # conversa real em 2026-09-10: sem esse campo, o especialista chamava
    # `get_user_allergies` com um `user_id` inventado em vez de admitir que
    # não tinha essa informação.
    usuario_id_postgres: int | None
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
    # Saída bruta de cada tool chamada pelo especialista nesta tentativa
    # (nome + retorno, ver `nodes/especialistas.py::_resposta_agente`) — dá
    # ao Agente Juiz (`nodes/juiz.py`) algo pra cruzar contra as afirmações
    # de `resposta_especialista`/`fontes_usadas`, em vez de só julgar se o
    # JSON "parece" coerente. Sem isto, um especialista podia citar uma tool
    # em `fontes_usadas` e inventar um dado que ela nunca devolveu (achado de
    # um teste de conversa real em 2026-09-10 — produto sem ingrediente
    # cadastrado, resposta "inventou" ingredientes, e o Juiz aprovou).
    evidencias_tools: list[dict[str, Any]] | None

    # --- agente juiz ---
    tentativas_juiz: int
    aprovado_juiz: bool | None
    feedback_juiz: str | None

    # --- orquestrador ---
    resposta_final: str | None

    # --- guardrail de saída ---
    saida_bloqueada: bool
