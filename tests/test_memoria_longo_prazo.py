"""Testes da memória de longo prazo (`nodes/memoria.py` + `memory/store.py`)
— por `usuario_id`, distinta do checkpointer por `thread_id` (ver
`tests/test_memoria.py`)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from venus_sdk.flows.venus_flow import compilar_grafo_venus
from venus_sdk.memory import criar_checkpointer_em_memoria, criar_store_em_memoria
from venus_sdk.nodes.memoria import no_atualizar_memoria, no_carregar_memoria


def _resposta_llm(texto: str) -> SimpleNamespace:
    return SimpleNamespace(content=texto)


# --- no_carregar_memoria ---


def test_carregar_memoria_sem_store_nao_faz_nada() -> None:
    assert no_carregar_memoria({"usuario_id": "u1"}, store=None) == {}


def test_carregar_memoria_sem_usuario_id_nao_faz_nada() -> None:
    store = criar_store_em_memoria()
    assert no_carregar_memoria({}, store=store) == {}


def test_carregar_memoria_usuario_novo_devolve_none() -> None:
    store = criar_store_em_memoria()
    resultado = no_carregar_memoria({"usuario_id": "u1"}, store=store)
    assert resultado == {"memorias_usuario": None}


def test_carregar_memoria_devolve_perfil_salvo() -> None:
    store = criar_store_em_memoria()
    store.put(("memorias", "u1"), "perfil", {"tipo_pele": "oleosa"})

    resultado = no_carregar_memoria({"usuario_id": "u1"}, store=store)

    assert resultado == {"memorias_usuario": {"tipo_pele": "oleosa"}}


def test_carregar_memoria_nao_vaza_entre_usuarios() -> None:
    store = criar_store_em_memoria()
    store.put(("memorias", "u1"), "perfil", {"tipo_pele": "oleosa"})

    resultado = no_carregar_memoria({"usuario_id": "u2"}, store=store)

    assert resultado == {"memorias_usuario": None}


# --- no_atualizar_memoria ---


def test_atualizar_memoria_sem_store_nao_faz_nada() -> None:
    assert no_atualizar_memoria({"usuario_id": "u1"}, store=None) == {}


def test_atualizar_memoria_sem_usuario_id_nao_faz_nada() -> None:
    store = criar_store_em_memoria()
    assert no_atualizar_memoria({}, store=store) == {}


def test_atualizar_memoria_llm_diz_nada_nao_grava() -> None:
    store = criar_store_em_memoria()
    with patch("venus_sdk.nodes.memoria.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm("NADA")
        resultado = no_atualizar_memoria(
            {"usuario_id": "u1", "pergunta_original": "oi", "resposta_final": "Oi!"},
            store=store,
        )

    assert resultado == {}
    assert store.get(("memorias", "u1"), "perfil") is None


def test_atualizar_memoria_grava_fatos_novos_e_mescla_com_perfil_atual() -> None:
    store = criar_store_em_memoria()
    store.put(("memorias", "u1"), "perfil", {"nome": "Sophia"})

    with patch("venus_sdk.nodes.memoria.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm('{"tipo_pele": "oleosa"}')
        resultado = no_atualizar_memoria(
            {
                "usuario_id": "u1",
                "memorias_usuario": {"nome": "Sophia"},
                "pergunta_original": "tenho pele oleosa",
                "resposta_final": "Anotado!",
            },
            store=store,
        )

    esperado = {"nome": "Sophia", "tipo_pele": "oleosa"}
    assert resultado == {"memorias_usuario": esperado}
    assert store.get(("memorias", "u1"), "perfil").value == esperado


def test_atualizar_memoria_ignora_texto_que_nao_e_json_nem_nada() -> None:
    store = criar_store_em_memoria()
    with patch("venus_sdk.nodes.memoria.get_llm_rapido") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm("isso não é JSON nem NADA")
        resultado = no_atualizar_memoria(
            {"usuario_id": "u1", "pergunta_original": "oi", "resposta_final": "Oi!"},
            store=store,
        )

    assert resultado == {}
    assert store.get(("memorias", "u1"), "perfil") is None


# --- integração: sobrevive à troca de thread_id (ao contrário do checkpointer) ---


def test_memoria_de_longo_prazo_sobrevive_a_troca_de_thread_id() -> None:
    grafo = compilar_grafo_venus(
        checkpointer=criar_checkpointer_em_memoria(),
        store=criar_store_em_memoria(),
    )

    with (
        patch("venus_sdk.nodes.roteador.get_llm_rapido") as roteador_mock,
        patch("venus_sdk.nodes.memoria.get_llm_rapido") as memoria_mock,
    ):
        roteador_mock.return_value.invoke.return_value = _resposta_llm("Oii Sophia! Tudo bem??")
        memoria_mock.return_value.invoke.return_value = _resposta_llm(
            '{"nome": "Sophia", "tipo_pele": "oleosa"}'
        )
        grafo.invoke(
            {"mensagem_usuario": "oi, sou a Sophia e tenho pele oleosa", "usuario_id": "u1"},
            config={"configurable": {"thread_id": "conversa-1"}},
        )

        # thread_id (conversa) diferente, mesmo usuario_id — o checkpointer
        # não conecta as duas conversas, mas a memória de longo prazo sim.
        roteador_mock.return_value.invoke.return_value = _resposta_llm("Oi de novo!")
        memoria_mock.return_value.invoke.return_value = _resposta_llm("NADA")
        estado_2 = grafo.invoke(
            {"mensagem_usuario": "oi de novo", "usuario_id": "u1"},
            config={"configurable": {"thread_id": "conversa-2"}},
        )

    assert estado_2["memorias_usuario"] == {"nome": "Sophia", "tipo_pele": "oleosa"}


# Sem teste automatizado para `criar_store_mongo()`: mesma razão do
# checkpointer (ver `tests/test_memoria.py`) — fala com o Atlas de verdade
# (`MONGODB_URI`), e o usuário configurado não tem permissão de
# `dropDatabase`. Testado manualmente (ping + put/get round-trip) na hora da
# implementação; ver PR/commit.
