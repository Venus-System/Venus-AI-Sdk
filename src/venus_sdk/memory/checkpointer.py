"""Checkpointer do grafo principal — persistência de contexto/histórico
entre execuções, por `thread_id` (uma conversa).

Duas opções hoje:

- `criar_checkpointer_em_memoria()` (`InMemorySaver`): só em RAM, dura
  enquanto o processo estiver de pé e some ao reiniciar. Bom para
  testes/dev rápido — não usar em produção.
- `criar_checkpointer_mongo()` (`MongoDBSaver`): persiste no MongoDB —
  sobrevive a restart do processo E funciona com múltiplas instâncias do
  SDK rodando ao mesmo tempo (é a opção pra produção, dado que o time já
  opera Mongo). Requer o extra `mongo` (ver `pyproject.toml`) —
  dependência pesada (`langchain-mongodb` traz junto `langchain`,
  `sqlalchemy`, `numpy`), por isso não vem instalada por padrão; só quem
  for usar Mongo paga esse custo.

(Chegamos a ter uma terceira opção em SQLite — descartada porque a
opção de produção já é o Mongo, e manter as duas persistentes era
redundante; ver histórico do git se precisar recuperar.)

Em qualquer um dos casos, o resto do SDK não muda, já que só depende da
interface `BaseCheckpointSaver` que `compilar_grafo_venus(checkpointer=...)`
espera.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.checkpoint.memory import InMemorySaver

from venus_sdk.config.settings import MONGODB_URI

if TYPE_CHECKING:
    from langgraph.checkpoint.mongodb import MongoDBSaver

DB_MONGO_PADRAO = "venus"


def criar_checkpointer_em_memoria() -> InMemorySaver:
    """Cria um checkpointer novo, em memória (RAM).

    Cada chamada devolve uma instância própria — não compartilhe uma mesma
    instância entre processos/threads que não devam ver a memória um do
    outro (ex.: testes).
    """
    return InMemorySaver()


def criar_checkpointer_mongo(
    uri: str | None = None,
    *,
    db_name: str = DB_MONGO_PADRAO,
    ttl_segundos: int | None = None,
) -> "MongoDBSaver":
    """Cria um checkpointer persistente, gravado no MongoDB.

    A opção pra produção: sobrevive a restart do processo e funciona com
    múltiplas instâncias do SDK rodando ao mesmo tempo, já que o Mongo é a
    fonte única de verdade compartilhada entre elas.

    `uri`: string de conexão do Mongo (ex.:
    `mongodb+srv://usuario:senha@host/`). Se omitida, usa `MONGODB_URI` do
    `.env` (ver `config/settings.py`).
    `db_name`: banco onde os checkpoints ficam — as coleções
    (`checkpoints`/`checkpoint_writes`) e os índices são criados
    automaticamente na primeira chamada.
    `ttl_segundos`: se definido, os checkpoints expiram sozinhos após esse
    tempo (ex.: `60 * 60 * 24` para expirar em 24h) — evita acumular
    conversas antigas pra sempre. Sem isso, ficam guardados
    indefinidamente.

    Conecta de verdade já na criação (ao contrário do `pymongo.MongoClient`
    puro, que é preguiçoso) — precisa de um Mongo alcançável nesse momento.
    Levanta `ImportError` com uma mensagem clara se o extra `mongo` não
    estiver instalado (`pip install venus-ai-sdk[mongo]`), e `ValueError`
    se nenhuma URI for encontrada.
    """
    try:
        from langgraph.checkpoint.mongodb import MongoDBSaver
        from pymongo import MongoClient
    except ImportError as erro:
        raise ImportError(
            "criar_checkpointer_mongo requer o extra 'mongo' — instale com "
            "`pip install venus-ai-sdk[mongo]` (ou "
            "`pip install langgraph-checkpoint-mongodb`)."
        ) from erro

    uri_final = uri or MONGODB_URI
    if not uri_final:
        raise ValueError(
            "Nenhuma URI de MongoDB configurada — defina MONGODB_URI no "
            ".env ou passe `uri=` explicitamente."
        )

    cliente = MongoClient(uri_final)
    return MongoDBSaver(cliente, db_name=db_name, ttl=ttl_segundos)
