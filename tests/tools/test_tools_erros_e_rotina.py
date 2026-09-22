"""Contrato das tools Postgres: nunca lista vazia silenciosa, nunca exceção
crua, busca sem acento; e as tools de rotina (com pool falso)."""

from __future__ import annotations

import asyncio

import pytest
from _fakes import ConexaoFalsa, PoolFalso

from venus_sdk.tools._util import normalizar_termo, sem_acento
from venus_sdk.tools.compartilhadas import montar_tools_compartilhadas
from venus_sdk.tools.ingrediente import montar_tools_ingrediente
from venus_sdk.tools.produto import montar_tools_produto
from venus_sdk.tools.rotina import montar_tools_rotina


def run(c):
    return asyncio.run(c)


def tools(fab, conexao):
    return {t.name: t for t in fab(PoolFalso(conexao))}


def test_normalizar_termo_remove_acentos() -> None:
    assert normalizar_termo("  Ácido Hialurônico ") == "Acido Hialuronico"
    assert "translate(p.name" in sem_acento("p.name")


@pytest.mark.parametrize("fab,nome,args", [
    (montar_tools_produto, "search_product", {"termo": "shampoo"}),
    (montar_tools_produto, "get_product_ingredients", {"product_id": 1}),
    (montar_tools_ingrediente, "search_ingredient", {"termo": "x"}),
    (montar_tools_ingrediente, "get_ingredient_properties", {"ingredient_id": 1}),
    (montar_tools_ingrediente, "get_ingredient_effects", {"ingredient_id": 1}),
    (montar_tools_ingrediente, "get_ingredient_regulations", {"ingredient_id": 1}),
    (montar_tools_rotina, "get_user_favorites", {"user_id": 1}),
    (montar_tools_rotina, "get_user_lists", {"user_id": 1}),
])
def test_resultado_vazio_e_estruturado_nunca_lista_vazia(fab, nome, args) -> None:
    r = run(tools(fab, ConexaoFalsa(fetch=[]))[nome].ainvoke(args))
    assert isinstance(r, dict) and r["encontrado"] is False and r["mensagem"]


@pytest.mark.parametrize("fab,nome,args", [
    (montar_tools_produto, "get_product", {"product_id": 1}),
    (montar_tools_produto, "search_product", {"termo": "shampoo"}),
    (montar_tools_ingrediente, "get_ingredient_summary", {"ingredient_id": 1}),
    (montar_tools_compartilhadas, "get_user_allergies", {"user_id": 1}),
    (montar_tools_rotina, "get_user_profile", {"user_id": 1}),
    (montar_tools_rotina, "add_favorite", {"user_id": 1, "product_id": 1}),
    (montar_tools_rotina, "suggest_routine", {"user_id": 1}),
])
def test_excecao_do_driver_vira_erro_estruturado(fab, nome, args) -> None:
    conexao = ConexaoFalsa(erro=ConnectionError("db fora do ar"))
    r = run(tools(fab, conexao)[nome].ainvoke(args))
    assert "erro" in r and r["detalhe"] == "ConnectionError"


def test_search_ignora_termo_vazio() -> None:
    conexao = ConexaoFalsa()
    r = run(tools(montar_tools_produto, conexao)["search_product"].ainvoke({"termo": "   "}))
    assert "erro" in r and conexao.chamadas == []


def test_search_produto_por_palavras_relevantes() -> None:
    from venus_sdk.tools._util import palavras_de_busca

    assert palavras_de_busca("produto bom pra cabelo cacheado") == ["cabelo", "cacheado"]
    assert palavras_de_busca("Sérum de Vitamina C") == ["serum", "vitamina"]
    assert palavras_de_busca("o de pra") == []
    conexao = ConexaoFalsa(fetch=[{"product_id": 1}])
    run(tools(montar_tools_produto, conexao)["search_product"].ainvoke({"termo": "produto bom pra cabelo cacheado"}))
    query, args = conexao.chamadas[0]
    assert args == (["cabelo", "cacheado"],) and "unnest($1::text[])" in query
    r = run(tools(montar_tools_produto, ConexaoFalsa())["search_product"].ainvoke({"termo": "produto bom pra"}))
    assert "erro" in r


def test_search_usa_termo_sem_acento_e_query_parametrizada() -> None:
    conexao = ConexaoFalsa(fetch=[{"ingredient_id": 1}])
    run(tools(montar_tools_ingrediente, conexao)["search_ingredient"].ainvoke({"termo": "Hialurônico'; DROP"}))
    query, args = conexao.chamadas[0]
    assert args == ("Hialuronico'; DROP",) and "$1" in query and "DROP" not in query
    assert "LIMIT 10" in query


def test_sem_alergias_e_resposta_valida() -> None:
    r = run(tools(montar_tools_compartilhadas, ConexaoFalsa(fetch=[]))["get_user_allergies"].ainvoke({"user_id": 5}))
    assert r["alergias"] == [] and r["encontrado"] is False


def test_decimal_e_datetime_viram_json_seguro() -> None:
    from datetime import datetime
    from decimal import Decimal

    linha = {"overall_score": Decimal("88.50"), "created_at": datetime(2026, 1, 2)}
    r = run(tools(montar_tools_produto, ConexaoFalsa(fetchrow=linha))["get_product_score"].ainvoke({"product_id": 1}))
    assert r == {"overall_score": 88.5, "created_at": "2026-01-02T00:00:00"}


def test_rotina_tools_sem_pool_levanta() -> None:
    with pytest.raises(ValueError, match="pool"):
        montar_tools_rotina(None)


def test_rotina_tem_as_6_tools() -> None:
    assert set(tools(montar_tools_rotina, ConexaoFalsa())) == {
        "get_user_profile", "get_user_favorites", "get_user_lists", "add_favorite",
        "remove_favorite", "suggest_routine"}


def test_remove_favorite_reporta_quando_nao_havia() -> None:
    t = tools(montar_tools_rotina, ConexaoFalsa(execute="DELETE 0"))["remove_favorite"]
    assert run(t.ainvoke({"user_id": 1, "product_id": 9}))["encontrado"] is False
    t = tools(montar_tools_rotina, ConexaoFalsa(execute="DELETE 1"))["remove_favorite"]
    assert run(t.ainvoke({"user_id": 1, "product_id": 9}))["ok"] is True


def test_add_favorite_produto_inexistente() -> None:
    r = run(tools(montar_tools_rotina, ConexaoFalsa(fetchrow=None))["add_favorite"].ainvoke({"user_id": 1, "product_id": 999}))
    assert r["encontrado"] is False


def _conexao_rotina(alergias):
    favoritos = [
        {"product_id": 1, "name": "Gel", "category_name": "Limpeza", "inci_names": ["aqua", "parfum"]},
        {"product_id": 2, "name": "Sérum", "category_name": "Sérum", "inci_names": ["aqua", "niacinamide"]},
        {"product_id": 3, "name": "Hidratante", "category_name": "Hidratante", "inci_names": ["aqua"]},
        {"product_id": 4, "name": "Protetor", "category_name": "Protetor solar", "inci_names": ["zinc oxide"]},
        {"product_id": 5, "name": "Retinol", "category_name": "Tratamento", "inci_names": ["retinol"]},
    ]
    return ConexaoFalsa(fetch=lambda q, *a: alergias if "allergy_name" in q else favoritos)


def test_suggest_routine_exclui_alergia_ordena_e_filtra_horario() -> None:
    conexao = _conexao_rotina([{"nome": "fragrância (parfum)"}])
    t = tools(montar_tools_rotina, conexao)["suggest_routine"]
    manha = run(t.ainvoke({"user_id": 1, "horario": "manha"}))
    assert [p["nome"] for p in manha["passos"]] == ["Sérum", "Hidratante", "Protetor"]
    assert manha["excluidos_por_alergia"][0]["product_id"] == 1
    assert manha["sem_produto_para"] == ["Limpeza"]
    noite = run(t.ainvoke({"user_id": 1, "horario": "noite"}))
    assert [p["nome"] for p in noite["passos"]] == ["Sérum", "Retinol", "Hidratante"]


def test_suggest_routine_horario_invalido_e_sem_favoritos() -> None:
    t = tools(montar_tools_rotina, _conexao_rotina([]))["suggest_routine"]
    assert "erro" in run(t.ainvoke({"user_id": 1, "horario": "tarde"}))
    vazio = tools(montar_tools_rotina, ConexaoFalsa(fetch=[]))["suggest_routine"]
    assert run(vazio.ainvoke({"user_id": 1}))["encontrado"] is False
