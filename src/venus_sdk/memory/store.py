"""Store do Venus — memória de longo prazo, por `usuario_id` (cross-thread).

Diferente do checkpointer (`memory/checkpointer.py`, que persiste o
histórico de UMA conversa por `thread_id`), o store guarda fatos/preferências
sobre a PESSOA — acessível em qualquer conversa futura dela, mesmo com
`thread_id` diferente. É a abstração `BaseStore` do LangGraph (ver
`nodes/memoria.py`, que é quem lê/escreve nele).

Duas opções, no mesmo espírito do checkpointer:

- `criar_store_em_memoria()` (`InMemoryStore`, embutido no LangGraph): só em
  RAM, some ao reiniciar o processo. Bom para testes/dev.
- `criar_store_mongo()` (`MongoDBStore`, definido abaixo): persiste no
  MongoDB — sobrevive a restart e funciona com múltiplas instâncias do SDK
  rodando ao mesmo tempo, igual ao `criar_checkpointer_mongo()`.

  O LangGraph publica um `checkpointer` oficial pra Mongo
  (`langgraph-checkpoint-mongodb`, usado em `checkpointer.py`), mas NÃO
  publica um `store` oficial pra Mongo (só tem `InMemoryStore` embutido e um
  `PostgresStore` num pacote separado) — por isso `MongoDBStore` aqui é
  implementação própria, do mesmo jeito que o time já confia no Mongo para
  o checkpointer. Requer o mesmo extra `mongo` do `pyproject.toml`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from langgraph.store.base import (
    BaseStore,
    GetOp,
    Item,
    ListNamespacesOp,
    MatchCondition,
    Op,
    PutOp,
    Result,
    SearchItem,
    SearchOp,
)
from langgraph.store.memory import InMemoryStore

from venus_sdk.config.settings import MONGODB_URI
from venus_sdk.memory.checkpointer import DB_MONGO_PADRAO

if TYPE_CHECKING:
    from pymongo import MongoClient
    from pymongo.collection import Collection

COLLECTION_MONGO_PADRAO = "memorias_longo_prazo"


def criar_store_em_memoria() -> InMemoryStore:
    """Cria um store novo, em memória (RAM) — não usar em produção.

    Cada chamada devolve uma instância própria — não compartilhe uma mesma
    instância entre processos/threads que não devam ver a memória um do
    outro (ex.: testes).
    """
    return InMemoryStore()


class MongoDBStore(BaseStore):
    """`BaseStore` do LangGraph persistido no MongoDB.

    Implementa só os dois métodos abstratos que `BaseStore` exige —
    `batch`/`abatch` — sobre os quais a classe-base já monta `get`, `put`,
    `delete`, `search` e `list_namespaces` (e as versões `a*`), então basta
    usar essas últimas normalmente (ver `nodes/memoria.py`).

    Sem indexação semântica (sem embeddings): `search()` filtra por
    namespace/`filter` exato e ordena por mais recente primeiro, sem ranking
    por similaridade (o parâmetro `query` de busca em linguagem natural é
    ignorado). Suficiente para o uso do Venus — perfil buscado por
    namespace+chave, não por busca livre — mas não é um vector store.
    """

    def __init__(
        self, cliente: "MongoClient", *, db_name: str, collection_name: str
    ) -> None:
        self._colecao: "Collection" = cliente[db_name][collection_name]
        self._colecao.create_index([("namespace", 1), ("key", 1)], unique=True)

    # --- API exigida por BaseStore ---

    def batch(self, ops: Iterable[Op]) -> list[Result]:
        return [self._executar(op) for op in ops]

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        # pymongo é síncrono; roda em thread pra não bloquear o event loop.
        return await asyncio.to_thread(self.batch, list(ops))

    # --- dispatch por tipo de operação ---

    def _executar(self, op: Op) -> Result:
        if isinstance(op, GetOp):
            return self._get(op)
        if isinstance(op, PutOp):
            return self._put(op)
        if isinstance(op, SearchOp):
            return self._search(op)
        if isinstance(op, ListNamespacesOp):
            return self._list_namespaces(op)
        raise TypeError(f"Operação não suportada por MongoDBStore: {type(op)!r}")

    def _get(self, op: GetOp) -> Item | None:
        doc = self._colecao.find_one({"namespace": list(op.namespace), "key": op.key})
        return self._doc_para_item(doc) if doc else None

    def _put(self, op: PutOp) -> None:
        filtro = {"namespace": list(op.namespace), "key": op.key}
        if op.value is None:
            self._colecao.delete_one(filtro)
            return None

        agora = datetime.now(timezone.utc)
        self._colecao.update_one(
            filtro,
            {
                "$set": {"value": op.value, "updated_at": agora},
                "$setOnInsert": {"created_at": agora},
            },
            upsert=True,
        )
        return None

    def _search(self, op: SearchOp) -> list[SearchItem]:
        consulta: dict[str, Any] = {}
        prefixo = list(op.namespace_prefix)
        if prefixo:
            # namespace começa com o prefixo dado (compara os N primeiros
            # elementos do array via aggregation expression, N = len(prefixo)).
            consulta["$expr"] = {"$eq": [{"$slice": ["$namespace", len(prefixo)]}, prefixo]}
        for campo, valor in (op.filter or {}).items():
            consulta[f"value.{campo}"] = valor

        cursor = (
            self._colecao.find(consulta)
            .sort("updated_at", -1)
            .skip(op.offset)
            .limit(op.limit)
        )
        return [self._doc_para_item(doc, buscado=True) for doc in cursor]

    def _list_namespaces(self, op: ListNamespacesOp) -> list[tuple[str, ...]]:
        namespaces = {tuple(doc["namespace"]) for doc in self._colecao.find({}, {"namespace": 1})}

        for condicao in op.match_conditions or ():
            namespaces = {ns for ns in namespaces if self._casa_condicao(ns, condicao)}

        if op.max_depth is not None:
            namespaces = {ns[: op.max_depth] for ns in namespaces}

        resultado = sorted(namespaces)
        return resultado[op.offset : op.offset + op.limit]

    @staticmethod
    def _casa_condicao(namespace: tuple[str, ...], condicao: MatchCondition) -> bool:
        caminho = [p for p in condicao.path if p != "*"]
        if len(caminho) > len(namespace):
            return False
        alvo = namespace[: len(caminho)] if condicao.match_type == "prefix" else namespace[-len(caminho):]
        return list(alvo) == caminho

    @staticmethod
    def _doc_para_item(doc: dict[str, Any], *, buscado: bool = False) -> Item:
        kwargs: dict[str, Any] = {
            "namespace": tuple(doc["namespace"]),
            "key": doc["key"],
            "value": doc["value"],
            "created_at": doc["created_at"],
            "updated_at": doc["updated_at"],
        }
        if buscado:
            return SearchItem(score=None, **kwargs)
        return Item(**kwargs)


def criar_store_mongo(
    uri: str | None = None,
    *,
    db_name: str = DB_MONGO_PADRAO,
    collection_name: str = COLLECTION_MONGO_PADRAO,
) -> MongoDBStore:
    """Cria um store de memória de longo prazo persistido no MongoDB.

    A opção pra produção: sobrevive a restart do processo e funciona com
    múltiplas instâncias do SDK ao mesmo tempo (mesmo banco `db_name` usado
    pelo checkpointer por padrão — `DB_MONGO_PADRAO` — mas em coleção própria,
    `collection_name`, então não colide com `checkpoints`/`checkpoint_writes`).

    `uri`: string de conexão do Mongo. Se omitida, usa `MONGODB_URI` do
    `.env` (ver `config/settings.py`) — a mesma variável usada por
    `criar_checkpointer_mongo()`.

    Levanta `ImportError` com mensagem clara se o extra `mongo` não estiver
    instalado, e `ValueError` se nenhuma URI for encontrada.
    """
    try:
        from pymongo import MongoClient
    except ImportError as erro:
        raise ImportError(
            "criar_store_mongo requer o extra 'mongo' — instale com "
            "`pip install venus-ai-sdk[mongo]` (ou `pip install pymongo`)."
        ) from erro

    uri_final = uri or MONGODB_URI
    if not uri_final:
        raise ValueError(
            "Nenhuma URI de MongoDB configurada — defina MONGODB_URI no "
            ".env ou passe `uri=` explicitamente."
        )

    cliente = MongoClient(uri_final)
    return MongoDBStore(cliente, db_name=db_name, collection_name=collection_name)
