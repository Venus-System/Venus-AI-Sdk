"""Governança da memória de longo prazo: alergia fora da memória, remoção de
campo, expiração (TTL) e exclusão (LGPD) — ver `nodes/memoria.py`."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from venus_sdk.memory import criar_store_em_memoria
from venus_sdk.nodes.memoria import apagar_memoria, no_atualizar_memoria, no_carregar_memoria

NS = ("memorias", "u1")


def _atualizar(store, saida_llm: str, memorias: dict | None = None) -> dict:
    with patch("venus_sdk.nodes.memoria.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = SimpleNamespace(content=saida_llm)
        return no_atualizar_memoria(
            {"usuario_id": "u1", "memorias_usuario": memorias, "mensagem_anonimizada": "msg", "resposta_final": "ok"},
            store=store,
        )


def test_alergia_dita_no_chat_nao_vira_memoria() -> None:
    store = criar_store_em_memoria()
    resultado = _atualizar(store, '{"alergias": ["fragrância"], "tipo_pele": "seca"}')
    assert resultado == {"memorias_usuario": {"tipo_pele": "seca"}}
    assert store.get(NS, "perfil").value == {"tipo_pele": "seca"}


def test_so_alergia_nao_grava_nada() -> None:
    store = criar_store_em_memoria()
    assert _atualizar(store, '{"alergias": ["fragrância"]}') == {}
    assert store.get(NS, "perfil") is None


def test_perfil_antigo_com_alergia_perde_o_campo_ao_carregar_e_ao_gravar() -> None:
    store = criar_store_em_memoria()
    store.put(NS, "perfil", {"tipo_pele": "oleosa", "alergias": ["parfum"]})
    assert no_carregar_memoria({"usuario_id": "u1"}, store=store) == {"memorias_usuario": {"tipo_pele": "oleosa"}}

    _atualizar(store, '{"nome": "Ana"}')
    assert store.get(NS, "perfil").value == {"tipo_pele": "oleosa", "nome": "Ana"}


def test_remover_apaga_o_campo_e_a_data_dele() -> None:
    store = criar_store_em_memoria()
    store.put(NS, "perfil", {"nome": "Ana", "objetivo": "tratar acne"})
    store.put(NS, "perfil_datas", {"nome": "2026-10-01", "objetivo": "2026-10-01"})

    resultado = _atualizar(store, '{"remover": ["objetivo"]}')

    assert resultado == {"memorias_usuario": {"nome": "Ana"}}
    assert store.get(NS, "perfil").value == {"nome": "Ana"}
    assert "objetivo" not in store.get(NS, "perfil_datas").value


def test_remover_em_formato_errado_e_ignorado() -> None:
    store = criar_store_em_memoria()
    store.put(NS, "perfil", {"nome": "Ana"})
    assert _atualizar(store, '{"remover": {"x": 1}}') == {}
    assert store.get(NS, "perfil").value == {"nome": "Ana"}


def test_grava_a_data_de_cada_campo_atualizado() -> None:
    store = criar_store_em_memoria()
    _atualizar(store, '{"tipo_pele": "seca"}')
    assert store.get(NS, "perfil_datas").value == {"tipo_pele": date.today().isoformat()}


def test_fato_nao_reafirmado_alem_do_ttl_expira(monkeypatch) -> None:
    monkeypatch.setenv("VENUS_MEMORIA_TTL_DIAS", "30")
    store = criar_store_em_memoria()
    antigo = (date.today() - timedelta(days=31)).isoformat()
    recente = (date.today() - timedelta(days=5)).isoformat()
    store.put(NS, "perfil", {"objetivo": "tratar acne", "tipo_pele": "oleosa", "nome": "Ana"})
    store.put(NS, "perfil_datas", {"objetivo": antigo, "tipo_pele": recente})

    # "nome" não tem data (gravado antes das datas existirem): não expira.
    assert no_carregar_memoria({"usuario_id": "u1"}, store=store) == {
        "memorias_usuario": {"tipo_pele": "oleosa", "nome": "Ana"}
    }


def test_ttl_zero_desliga_a_expiracao(monkeypatch) -> None:
    monkeypatch.setenv("VENUS_MEMORIA_TTL_DIAS", "0")
    store = criar_store_em_memoria()
    store.put(NS, "perfil", {"objetivo": "tratar acne"})
    store.put(NS, "perfil_datas", {"objetivo": "2020-01-01"})
    assert no_carregar_memoria({"usuario_id": "u1"}, store=store) == {"memorias_usuario": {"objetivo": "tratar acne"}}


def test_apagar_memoria_remove_perfil_e_datas() -> None:
    store = criar_store_em_memoria()
    store.put(NS, "perfil", {"nome": "Ana"})
    store.put(NS, "perfil_datas", {"nome": "2026-10-01"})
    store.put(("memorias", "u2"), "perfil", {"nome": "Bia"})

    apagar_memoria(store, "u1")

    assert store.get(NS, "perfil") is None and store.get(NS, "perfil_datas") is None
    assert no_carregar_memoria({"usuario_id": "u1"}, store=store) == {"memorias_usuario": None}
    assert store.get(("memorias", "u2"), "perfil").value == {"nome": "Bia"}
