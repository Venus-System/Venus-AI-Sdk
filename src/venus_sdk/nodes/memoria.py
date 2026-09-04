"""Nós de memória de longo prazo — perfil do usuário persistido entre
conversas (`thread_id`) diferentes, via `store` (ver `memory/store.py`).

Distinto do checkpointer (`memory/checkpointer.py`): o checkpointer guarda o
histórico bruto de UMA conversa (`thread_id`); estes nós guardam um resumo de
fatos duráveis sobre o usuário (`usuario_id`), consultável em qualquer
conversa futura dele.

`no_carregar_memoria` roda logo após o guardrail de entrada (antes do
roteador) e `no_atualizar_memoria` roda por último, depois do guardrail de
saída — ver `flows/venus_flow.py`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langgraph.store.base import BaseStore

from venus_sdk.llm.models import get_llm_rapido
from venus_sdk.prompts.memoria import MEMORIA_PROMPT_COMPLETO
from venus_sdk.state import EstadoVenus

logger = logging.getLogger(__name__)

NAMESPACE_MEMORIAS = "memorias"
CHAVE_PERFIL = "perfil"


def _namespace(usuario_id: str) -> tuple[str, str]:
    return (NAMESPACE_MEMORIAS, usuario_id)


def no_carregar_memoria(estado: EstadoVenus, *, store: BaseStore) -> EstadoVenus:
    """Busca o perfil de longo prazo do usuário (se houver) e grava em
    `memorias_usuario`, pra ser injetado no roteador/especialistas (ver
    `MEMORIA_USUARIO=` em `nodes/roteador.py`/`nodes/especialistas.py`).

    Sem `store` configurado (`compilar_grafo_venus(store=...)` não recebeu
    um) ou sem `usuario_id` no estado, não faz nada — a conversa segue sem
    memória de longo prazo, como antes de esses campos existirem.
    """
    usuario_id = estado.get("usuario_id")
    if store is None or not usuario_id:
        return {}

    item = store.get(_namespace(usuario_id), CHAVE_PERFIL)
    return {"memorias_usuario": item.value if item else None}


def no_atualizar_memoria(estado: EstadoVenus, *, store: BaseStore) -> EstadoVenus:
    """Ao final do turno, decide (via LLM) se algo dito nesta troca merece
    virar memória de longo prazo e, se sim, funde no perfil existente.

    Roda depois do guardrail de saída — não altera `resposta_final`, só
    atualiza o `store`. Qualquer falha aqui (LLM, parsing) é engolida: isso
    nunca deve derrubar uma resposta que já foi validada e está indo pro
    usuário.
    """
    usuario_id = estado.get("usuario_id")
    if store is None or not usuario_id:
        return {}

    pergunta = estado.get("pergunta_original") or estado.get("mensagem_usuario", "")
    resposta = estado.get("resposta_final") or ""
    perfil_atual = estado.get("memorias_usuario") or {}

    entrada = (
        f"PERFIL_ATUAL={json.dumps(perfil_atual, ensure_ascii=False)}\n"
        f"PERGUNTA_USUARIO={pergunta}\n"
        f"RESPOSTA_VENUS={resposta}"
    )
    mensagens = [("system", MEMORIA_PROMPT_COMPLETO), ("human", entrada)]

    try:
        texto = (get_llm_rapido().invoke(mensagens).content or "").strip()
    except Exception:
        logger.exception("Falha ao chamar o LLM extrator de memória (usuario_id=%s)", usuario_id)
        return {}

    fatos_novos = _extrair_fatos_novos(texto)
    if not fatos_novos:
        return {}

    perfil_atualizado = {**perfil_atual, **fatos_novos}
    store.put(_namespace(usuario_id), CHAVE_PERFIL, perfil_atualizado)
    return {"memorias_usuario": perfil_atualizado}


def _extrair_fatos_novos(texto: str) -> dict[str, Any] | None:
    if not texto or texto.strip().upper() == "NADA":
        return None
    try:
        fatos = json.loads(texto)
    except (TypeError, ValueError):
        logger.warning("Extrator de memória não devolveu JSON válido: %r", texto)
        return None
    return fatos if isinstance(fatos, dict) and fatos else None
