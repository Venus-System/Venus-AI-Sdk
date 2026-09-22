"""Integração contra um Postgres REAL com schema+seed (scripts/init_db.py).
Rode: `DATABASE_URL=... pytest -m integration`. Pulado por padrão."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration
asyncpg = pytest.importorskip("asyncpg")

from venus_sdk.tools.compartilhadas import montar_tools_compartilhadas  # noqa: E402
from venus_sdk.tools.ingrediente import montar_tools_ingrediente  # noqa: E402
from venus_sdk.tools.produto import montar_tools_produto  # noqa: E402
from venus_sdk.tools.rotina import montar_tools_rotina  # noqa: E402


@pytest.fixture
async def t():
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL não definida")
    pool = await asyncpg.create_pool(url)
    ts = {x.name: x for f in (montar_tools_produto, montar_tools_ingrediente, montar_tools_compartilhadas,
                              montar_tools_rotina) for x in f(pool)}
    yield ts
    await pool.close()


async def test_busca_sem_acento(t) -> None:
    r = await t["search_ingredient"].ainvoke({"termo": "hialuronico"})
    assert r[0]["inci_name"] == "Sodium Hyaluronate"
    assert (await t["search_ingredient"].ainvoke({"termo": "vitamina b3"}))[0]["common_name"] == "Niacinamida"
    assert len(await t["search_product"].ainvoke({"termo": "serum"})) == 2


async def test_produto_sem_ingrediente_nao_inventa(t) -> None:
    r = await t["get_product_ingredients"].ainvoke({"product_id": 10})
    assert r["encontrado"] is False
    assert (await t["get_product_score"].ainvoke({"product_id": 10}))["encontrado"] is False


async def test_todas_as_tools_de_leitura(t) -> None:
    assert (await t["get_product"].ainvoke({"product_id": 2}))["brand_name"] == "DermaClara"
    assert (await t["get_product_score"].ainvoke({"product_id": 2}))["overall_score"] == 88.0
    assert (await t["get_personalized_score"].ainvoke({"product_id": 2, "user_id": 1}))["risk_level"] == "baixo"
    assert len(await t["get_product_ingredients"].ainvoke({"product_id": 2})) == 4
    assert (await t["get_ingredient_summary"].ainvoke({"ingredient_id": 1}))["source_reference"] == "CosIng"
    assert (await t["get_ingredient_properties"].ainvoke({"ingredient_id": 1}))[0]["unit"] == "g/mol"
    assert (await t["get_ingredient_effects"].ainvoke({"ingredient_id": 1, "profile_tag": "pele oleosa"}))[0]["effect_name"]
    assert (await t["get_ingredient_regulations"].ainvoke({"ingredient_id": 5}))[0]["agency"] == "ANVISA"
    assert (await t["get_user_allergies"].ainvoke({"user_id": 1}))[0]["severity"] == "moderada"
    assert (await t["get_user_profile"].ainvoke({"user_id": 1}))["skin_type"] == "oleosa"
    assert len(await t["get_user_favorites"].ainvoke({"user_id": 1})) == 5
    assert (await t["get_user_lists"].ainvoke({"user_id": 1}))[0]["list_name"] == "Rotina de manhã"


async def test_rotina_respeita_alergia(t) -> None:
    r = await t["suggest_routine"].ainvoke({"user_id": 1, "horario": "manha"})
    excluidos = {e["product_id"] for e in r["excluidos_por_alergia"]}
    assert excluidos == {1, 4}  # ambos têm Parfum e o usuário 1 é alérgico
    assert 1 not in {p["product_id"] for p in r["passos"]}


async def test_favoritos_escrita(t) -> None:
    assert (await t["add_favorite"].ainvoke({"user_id": 2, "product_id": 6}))["ok"]
    assert (await t["add_favorite"].ainvoke({"user_id": 2, "product_id": 6}))["ok"]  # idempotente
    assert (await t["remove_favorite"].ainvoke({"user_id": 2, "product_id": 6}))["ok"]
    assert (await t["remove_favorite"].ainvoke({"user_id": 2, "product_id": 6}))["encontrado"] is False
