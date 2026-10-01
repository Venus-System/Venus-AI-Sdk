"""`MongoDBStore` contra uma coleção falsa que imita o Mongo no que importa:
índice único MULTIKEY (campo lista indexado item a item), índice parcial,
`DuplicateKeyError` e upsert. Sem Mongo real no CI — mas o bug do índice
(só o 1º usuário conseguia gravar) é reproduzido aqui e fica coberto."""

from __future__ import annotations

import itertools
from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage
from pymongo.errors import DuplicateKeyError, OperationFailure

from venus_sdk.memory.store import MongoDBStore
from venus_sdk.nodes.memoria import no_atualizar_memoria, no_carregar_memoria

_AUSENTE = object()


class _Cursor(list):
    def sort(self, campo: str, direcao: int) -> _Cursor:
        return _Cursor(sorted(self, key=lambda d: d.get(campo), reverse=direcao < 0))

    def skip(self, n: int) -> _Cursor:
        return _Cursor(self[n:])

    def limit(self, n: int) -> _Cursor:
        return _Cursor(self[:n])


class ColecaoFalsa:
    """Subconjunto do `Collection` do pymongo usado pelo `MongoDBStore`."""

    name = "memorias_longo_prazo"

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []
        self.indices: dict[str, dict[str, Any]] = {"_id_": {"key": [("_id", 1)]}}
        self._ids = itertools.count(1)
        self.falhas_de_upsert = 0  # simula corrida entre instâncias

    # --- índices ---

    def index_information(self) -> dict[str, dict[str, Any]]:
        return {nome: dict(info) for nome, info in self.indices.items()}

    def create_index(self, chaves, *, name=None, unique=False, partialFilterExpression=None):
        nome = name or "_".join(f"{campo}_{direcao}" for campo, direcao in chaves)
        info = {"key": list(chaves), "unique": unique, "partialFilterExpression": partialFilterExpression}
        if nome in self.indices and self.indices[nome] != info:
            raise OperationFailure(f"IndexOptionsConflict: {nome}")
        self.indices[nome] = info
        return nome

    def drop_index(self, nome: str) -> None:
        if nome not in self.indices:
            raise OperationFailure(f"index not found with name [{nome}]")
        del self.indices[nome]

    # --- consultas ---

    @staticmethod
    def _casa(doc: dict[str, Any], filtro: dict[str, Any]) -> bool:
        for campo, esperado in filtro.items():
            valor = doc.get(campo, _AUSENTE)
            if isinstance(esperado, dict) and "$exists" in esperado:
                if (valor is not _AUSENTE) != esperado["$exists"]:
                    return False
            elif valor != esperado:
                return False
        return True

    def find(self, filtro=None, projecao=None) -> _Cursor:
        return _Cursor(dict(d) for d in self.docs if self._casa(d, filtro or {}))

    def find_one(self, filtro):
        return next(iter(self.find(filtro)), None)

    def delete_one(self, filtro) -> None:
        for doc in self.docs:
            if self._casa(doc, filtro):
                self.docs.remove(doc)
                return

    def update_one(self, filtro, atualizacao, upsert=False) -> None:
        if upsert and self.falhas_de_upsert:
            self.falhas_de_upsert -= 1
            raise DuplicateKeyError("E11000 (corrida simulada)")
        alvo = next((d for d in self.docs if self._casa(d, filtro)), None)
        if alvo is None:
            if not upsert:
                return
            novo = {"_id": next(self._ids), **{k: v for k, v in filtro.items() if not isinstance(v, dict)}}
            novo.update(atualizacao.get("$setOnInsert", {}))
            novo.update(atualizacao.get("$set", {}))
            self._checar_unicos(novo, ignorar=None)
            self.docs.append(novo)
            return
        candidato = {**alvo, **atualizacao.get("$set", {})}
        self._checar_unicos(candidato, ignorar=alvo)
        alvo.update(atualizacao.get("$set", {}))

    # --- índice único multikey, como o Mongo ---

    @staticmethod
    def _entradas(doc: dict[str, Any], chaves) -> set[tuple]:
        valores = []
        for campo, _ in chaves:
            valor = doc.get(campo)
            valores.append(valor if isinstance(valor, list) else [valor])
        return set(itertools.product(*valores))

    def _checar_unicos(self, doc: dict[str, Any], ignorar) -> None:
        for nome, info in self.indices.items():
            if not info.get("unique"):
                continue
            parcial = info.get("partialFilterExpression")
            if parcial and not self._casa(doc, parcial):
                continue
            entradas = self._entradas(doc, info["key"])
            for outro in self.docs:
                if outro is ignorar or (parcial and not self._casa(outro, parcial)):
                    continue
                if entradas & self._entradas(outro, info["key"]):
                    raise DuplicateKeyError(f"E11000 duplicate key error index: {nome}")


class _Cliente:
    def __init__(self, colecao: ColecaoFalsa) -> None:
        self._colecao = colecao

    def __getitem__(self, _nome):
        colecao = self._colecao

        class _Banco:
            def __getitem__(self, _nome_colecao):
                return colecao

        return _Banco()


def _store(colecao: ColecaoFalsa | None = None) -> tuple[MongoDBStore, ColecaoFalsa]:
    colecao = colecao or ColecaoFalsa()
    return MongoDBStore(_Cliente(colecao), db_name="venus", collection_name=colecao.name), colecao


# --- o bug e a correção ---


def test_colecao_falsa_reproduz_o_bug_do_indice_antigo() -> None:
    """Esquema antigo: índice único em (namespace LISTA, key) — o 2º usuário estoura."""
    colecao = ColecaoFalsa()
    colecao.create_index([("namespace", 1), ("key", 1)], unique=True)
    colecao.update_one({"namespace": ["memorias", "uid-A"], "key": "perfil"}, {"$set": {"value": {}}}, upsert=True)
    with pytest.raises(DuplicateKeyError):
        colecao.update_one({"namespace": ["memorias", "uid-B"], "key": "perfil"}, {"$set": {"value": {}}}, upsert=True)


def test_varios_usuarios_gravam_e_leem_a_propria_memoria() -> None:
    store, _ = _store()
    for uid, nome in (("uid-A", "Ana"), ("uid-B", "Bia"), ("uid-C", "Cris")):
        store.put(("memorias", uid), "perfil", {"nome": nome})
    assert store.get(("memorias", "uid-A"), "perfil").value == {"nome": "Ana"}
    assert store.get(("memorias", "uid-B"), "perfil").value == {"nome": "Bia"}
    assert store.get(("memorias", "uid-C"), "perfil").value == {"nome": "Cris"}


def test_mesmo_usuario_atualiza_sem_duplicar() -> None:
    store, colecao = _store()
    store.put(("memorias", "uid-A"), "perfil", {"nome": "Ana"})
    store.put(("memorias", "uid-A"), "perfil", {"nome": "Ana", "tipo_pele": "oleosa"})
    assert len(colecao.docs) == 1
    assert store.get(("memorias", "uid-A"), "perfil").value["tipo_pele"] == "oleosa"


def test_colecao_antiga_e_migrada_na_inicializacao_sem_passo_manual() -> None:
    colecao = ColecaoFalsa()
    colecao.create_index([("namespace", 1), ("key", 1)], unique=True)  # índice legado
    colecao.docs.append({"_id": 99, "namespace": ["memorias", "uid-A"], "key": "perfil",
                         "value": {"nome": "Ana"}, "created_at": 1, "updated_at": 1})

    store, _ = _store(colecao)

    assert "namespace_1_key_1" not in colecao.indices
    assert colecao.indices["namespace_str_1_key_1"]["unique"] is True
    assert store.get(("memorias", "uid-A"), "perfil").value == {"nome": "Ana"}  # doc antigo continua legível
    store.put(("memorias", "uid-B"), "perfil", {"nome": "Bia"})                  # e o 2º usuário grava
    assert store.get(("memorias", "uid-B"), "perfil").value == {"nome": "Bia"}


def test_duas_instancias_subindo_juntas_nao_quebram() -> None:
    colecao = ColecaoFalsa()
    colecao.create_index([("namespace", 1), ("key", 1)], unique=True)
    _store(colecao)
    _store(colecao)  # 2ª instância: índice legado já removido, índice novo já existe
    assert set(colecao.indices) == {"_id_", "namespace_str_1_key_1"}


def test_instancia_que_perde_a_corrida_para_remover_o_indice_legado_segue_normal() -> None:
    colecao = ColecaoFalsa()
    colecao.create_index([("namespace", 1), ("key", 1)], unique=True)
    original = colecao.drop_index

    def _outra_instancia_removeu_antes(nome):
        original(nome)
        raise OperationFailure("index not found")

    colecao.drop_index = _outra_instancia_removeu_antes
    _store(colecao)
    assert "namespace_str_1_key_1" in colecao.indices


def test_upsert_concorrente_tenta_de_novo_em_vez_de_falhar() -> None:
    store, colecao = _store()
    colecao.falhas_de_upsert = 1  # outra instância inseriu o mesmo doc no mesmo instante
    store.put(("memorias", "uid-A"), "perfil", {"nome": "Ana"})
    assert store.get(("memorias", "uid-A"), "perfil").value == {"nome": "Ana"}


def test_delete_remove_so_o_documento_do_usuario() -> None:
    store, _ = _store()
    store.put(("memorias", "uid-A"), "perfil", {"nome": "Ana"})
    store.put(("memorias", "uid-B"), "perfil", {"nome": "Bia"})
    store.delete(("memorias", "uid-A"), "perfil")
    assert store.get(("memorias", "uid-A"), "perfil") is None
    assert store.get(("memorias", "uid-B"), "perfil").value == {"nome": "Bia"}


# --- falha do store nunca derruba a resposta ---


class _StoreQueQuebra:
    def get(self, *args, **kwargs):
        raise TimeoutError("Mongo não respondeu")

    def put(self, *args, **kwargs):
        raise DuplicateKeyError("E11000")


def test_falha_ao_ler_memoria_segue_o_turno_sem_memoria() -> None:
    assert no_carregar_memoria({"usuario_id": "uid-B"}, store=_StoreQueQuebra()) == {}


def test_falha_ao_gravar_memoria_nao_derruba_a_resposta() -> None:
    class _LLM:
        def invoke(self, mensagens):
            return AIMessage(content='{"nome": "Bia"}')

    estado = {"usuario_id": "uid-B", "mensagem_usuario": "meu nome é Bia", "resposta_final": "Prazer, Bia!"}
    with patch("venus_sdk.nodes.memoria.get_llm_rapido", return_value=_LLM()):
        assert no_atualizar_memoria(estado, store=_StoreQueQuebra()) == {}
