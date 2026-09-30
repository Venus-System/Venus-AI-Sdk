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
import json
import logging
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

from venus_sdk.memory.checkpointer import DB_MONGO_PADRAO, TIMEOUT_MONGO_MS_PADRAO, _cliente_mongo

if TYPE_CHECKING:
    from pymongo import MongoClient
    from pymongo.collection import Collection

logger = logging.getLogger(__name__)

COLLECTION_MONGO_PADRAO = "memorias_longo_prazo"

# Índice único antigo em (namespace, key). `namespace` é uma LISTA, então o
# Mongo cria índice multikey — indexa cada item separado — e todo usuário
# gerava a mesma entrada ("memorias", "perfil"): o `unique` barrava do
# segundo usuário em diante (DuplicateKeyError). Removido na inicialização.
_INDICE_LEGADO = "namespace_1_key_1"
_INDICE_UNICO = "namespace_str_1_key_1"


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

    def __init__(self, cliente: MongoClient, *, db_name: str, collection_name: str) -> None:
        self._colecao: Collection = cliente[db_name][collection_name]
        self._preparar_colecao()

    # --- índice e migração (seguros com várias instâncias subindo juntas) ---

    def _preparar_colecao(self) -> None:
        """Deixa a coleção pronta: remove o índice único legado, preenche
        `namespace_str` nos documentos antigos e cria o índice único novo.

        Tudo idempotente e tolerante a concorrência: várias tasks da API podem
        subir ao mesmo tempo (ou conviver com a versão antiga num deploy
        gradual) sem passo manual nem erro de "índice já existe/não existe"."""
        self._remover_indice_legado()
        self._preencher_namespace_str()
        self._colecao.create_index(
            [("namespace_str", 1), ("key", 1)],
            name=_INDICE_UNICO,
            unique=True,
            # Documento gravado pela versão antiga (sem o campo) não entra no
            # índice — sem isso, todos colidiriam no valor nulo.
            partialFilterExpression={"namespace_str": {"$exists": True}},
        )

    def _remover_indice_legado(self) -> None:
        from pymongo.errors import OperationFailure

        indice = self._colecao.index_information().get(_INDICE_LEGADO)
        if not indice or not indice.get("unique"):
            return
        try:
            self._colecao.drop_index(_INDICE_LEGADO)
            logger.info("Índice único legado %s removido de %s", _INDICE_LEGADO, self._colecao.name)
        except OperationFailure:
            # Outra instância removeu antes — o resultado é o mesmo.
            logger.info("Índice %s já tinha sido removido por outra instância", _INDICE_LEGADO)

    def _preencher_namespace_str(self) -> None:
        for doc in self._colecao.find({"namespace_str": {"$exists": False}}, {"namespace": 1}):
            self._colecao.update_one(
                {"_id": doc["_id"]}, {"$set": {"namespace_str": _chave_do_namespace(doc["namespace"])}}
            )

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
        doc = self._colecao.find_one(_filtro(op.namespace, op.key))
        return self._doc_para_item(doc) if doc else None

    def _put(self, op: PutOp) -> None:
        filtro = _filtro(op.namespace, op.key)
        if op.value is None:
            self._colecao.delete_one(filtro)
            return

        agora = datetime.now(timezone.utc)
        atualizacao = {
            "$set": {"value": op.value, "updated_at": agora},
            # `namespace` (lista) continua gravado: é o que `_search` usa para
            # filtrar por prefixo e o que a versão antiga lê.
            "$setOnInsert": {"created_at": agora, "namespace": list(op.namespace)},
        }
        self._upsert(filtro, atualizacao)

    def _upsert(self, filtro: dict[str, Any], atualizacao: dict[str, Any]) -> None:
        """`update_one(upsert=True)` com uma nova tentativa: quando duas
        instâncias inserem o MESMO documento ao mesmo tempo, uma delas recebe
        `DuplicateKeyError` do índice único — na segunda tentativa o documento
        já existe e vira um update comum."""
        from pymongo.errors import DuplicateKeyError

        try:
            self._colecao.update_one(filtro, atualizacao, upsert=True)
        except DuplicateKeyError:
            self._colecao.update_one(filtro, atualizacao, upsert=True)

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


def _chave_do_namespace(namespace: Iterable[str]) -> str:
    """Namespace como texto único e sem ambiguidade (`["memorias", "uid"]`),
    para o índice único — um campo LISTA viraria índice multikey."""
    return json.dumps(list(namespace), ensure_ascii=False)


def _filtro(namespace: Iterable[str], key: str) -> dict[str, Any]:
    return {"namespace_str": _chave_do_namespace(namespace), "key": key}


def criar_store_mongo(
    uri: str | None = None,
    *,
    db_name: str = DB_MONGO_PADRAO,
    collection_name: str = COLLECTION_MONGO_PADRAO,
    timeout_ms: int = TIMEOUT_MONGO_MS_PADRAO,
) -> MongoDBStore:
    """Cria um store de memória de longo prazo persistido no MongoDB.

    A opção pra produção: sobrevive a restart do processo e funciona com
    múltiplas instâncias do SDK ao mesmo tempo (mesmo banco `db_name` usado
    pelo checkpointer por padrão — `DB_MONGO_PADRAO` — mas em coleção própria,
    `collection_name`, então não colide com `checkpoints`/`checkpoint_writes`).

    `uri`: string de conexão do Mongo. Se omitida, usa `MONGODB_URI` do
    `.env` (ver `config/settings.py`) — a mesma variável usada por
    `criar_checkpointer_mongo()`.

    `timeout_ms`: quanto esperar para achar/conectar ao servidor antes de
    desistir. Curto de propósito — quem chama (`nodes/memoria.py`) segue a
    conversa sem memória quando o Mongo não responde.

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

    cliente = _cliente_mongo(MongoClient, uri, timeout_ms)
    return MongoDBStore(cliente, db_name=db_name, collection_name=collection_name)
