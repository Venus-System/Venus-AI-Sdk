"""Usuário da conversa em andamento, para as tools que leem dados da conta.

O `user_id` que uma tool recebe é escolhido pelo LLM — e uma injeção de
prompt ("na verdade meu id é 2") pode trocá-lo. Dentro do grafo, o
especialista fixa aqui o `usuario_id_postgres` do estado (`usuario_da_conversa`)
e as tools usam ESSE valor (`resolver_user_id`), nunca o informado pelo LLM.

Fora do grafo (chamada direta da tool, testes) nada é fixado e vale o
`user_id` informado — quem chama responde pela identidade."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

logger = logging.getLogger(__name__)

_FORA_DO_GRAFO = object()
_usuario_fixado: ContextVar[object] = ContextVar("venus_usuario_da_conversa", default=_FORA_DO_GRAFO)

SEM_USUARIO_IDENTIFICADO = {
    "erro": "usuário não identificado nesta conversa — não é possível consultar dados da conta",
}


@contextmanager
def usuario_da_conversa(user_id: int | None) -> Iterator[None]:
    """Fixa o usuário da conversa (ou `None`: conversa sem usuário
    identificado) enquanto o bloco roda."""
    token = _usuario_fixado.set(user_id)
    try:
        yield
    finally:
        _usuario_fixado.reset(token)


def resolver_user_id(informado: int) -> int | None:
    """O `user_id` que a tool deve consultar: o da conversa, se houver um
    fixado; `None` se a conversa não tem usuário (a tool não consulta nada);
    o `informado` só fora do grafo."""
    fixado = _usuario_fixado.get()
    if fixado is _FORA_DO_GRAFO:
        return informado
    if fixado is not None and informado != fixado:
        logger.warning("Tool pediu user_id %s, diferente do usuário da conversa; usando o da conversa", informado)
    return fixado  # type: ignore[return-value]
