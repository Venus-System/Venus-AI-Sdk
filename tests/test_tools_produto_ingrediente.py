"""Testes das tools de Produto/Ingrediente/Compartilhada (`tools/`).

Usa um pool `asyncpg` falso (sem rede) — valida a forma da query/resposta de
cada tool, não o SQL contra um Postgres real. Sem teste automatizado contra
o Postgres real: o usuário do `.env` (`api_ia`) tem `permission denied for
schema venus` em qualquer `SELECT` hoje (falta `GRANT` de quem administra o
banco) — ver `tools/produto.py` para o `GRANT` necessário."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from venus_sdk.tools.compartilhadas import montar_tools_compartilhadas
from venus_sdk.tools.ingrediente import montar_tools_ingrediente
from venus_sdk.tools.produto import montar_tools_produto


class _ConexaoFalsa:
    """Substitui uma conexão `asyncpg` — devolve resultados fixos e grava
    toda query/args recebida em `chamadas`, pra inspeção no teste."""

    def __init__(self, fetchrow_result: dict | None = None, fetch_result: list[dict] | None = None) -> None:
        self.fetchrow_result = fetchrow_result
        self.fetch_result = fetch_result if fetch_result is not None else []
        self.chamadas: list[tuple[str, tuple]] = []

    async def fetchrow(self, query: str, *args: Any) -> dict | None:
        self.chamadas.append((query, args))
        return self.fetchrow_result

    async def fetch(self, query: str, *args: Any) -> list[dict]:
        self.chamadas.append((query, args))
        return self.fetch_result


class _AcquireFalso:
    def __init__(self, conexao: _ConexaoFalsa) -> None:
        self._conexao = conexao

    async def __aenter__(self) -> _ConexaoFalsa:
        return self._conexao

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _PoolFalso:
    """Substitui `asyncpg.Pool` — só precisa de `.acquire()`."""

    def __init__(self, conexao: _ConexaoFalsa) -> None:
        self._conexao = conexao

    def acquire(self) -> _AcquireFalso:
        return _AcquireFalso(self._conexao)


def _rodar(coro):
    return asyncio.run(coro)


def _tool(tools: list, nome: str):
    return next(t for t in tools if t.name == nome)


# --- sem pool: erro imediato, não silencioso ---


@pytest.mark.parametrize(
    "montar",
    [montar_tools_produto, montar_tools_ingrediente, montar_tools_compartilhadas],
)
def test_montar_tools_sem_pool_levanta_value_error(montar) -> None:
    with pytest.raises(ValueError, match="pool"):
        montar(None)


# --- produto ---


def test_tools_produto_tem_os_4_nomes_esperados() -> None:
    nomes = {t.name for t in montar_tools_produto(_PoolFalso(_ConexaoFalsa()))}
    assert nomes == {
        "get_product",
        "get_product_score",
        "get_personalized_score",
        "get_product_ingredients",
    }


def test_get_product_encontrado() -> None:
    conexao = _ConexaoFalsa(fetchrow_result={"name": "Sérum X", "brand_name": "Marca Y"})
    tools = montar_tools_produto(_PoolFalso(conexao))

    resultado = _rodar(_tool(tools, "get_product").ainvoke({"product_id": 1}))

    assert resultado == {"name": "Sérum X", "brand_name": "Marca Y"}
    assert conexao.chamadas[0][1] == (1,)


def test_get_product_nao_encontrado() -> None:
    conexao = _ConexaoFalsa(fetchrow_result=None)
    tools = montar_tools_produto(_PoolFalso(conexao))

    resultado = _rodar(_tool(tools, "get_product").ainvoke({"product_id": 999}))

    assert resultado == {"erro": "produto não encontrado"}


def test_get_personalized_score_passa_product_id_e_user_id() -> None:
    conexao = _ConexaoFalsa(fetchrow_result={"final_score": 87})
    tools = montar_tools_produto(_PoolFalso(conexao))

    resultado = _rodar(
        _tool(tools, "get_personalized_score").ainvoke({"product_id": 1, "user_id": 42})
    )

    assert resultado == {"final_score": 87}
    assert conexao.chamadas[0][1] == (1, 42)


def test_get_product_ingredients_devolve_lista() -> None:
    conexao = _ConexaoFalsa(fetch_result=[{"position": 1, "common_name": "Água"}])
    tools = montar_tools_produto(_PoolFalso(conexao))

    resultado = _rodar(_tool(tools, "get_product_ingredients").ainvoke({"product_id": 1}))

    assert resultado == [{"position": 1, "common_name": "Água"}]


# --- ingrediente ---


def test_tools_ingrediente_tem_os_5_nomes_esperados() -> None:
    nomes = {t.name for t in montar_tools_ingrediente(_PoolFalso(_ConexaoFalsa()))}
    assert nomes == {
        "search_ingredient",
        "get_ingredient_summary",
        "get_ingredient_properties",
        "get_ingredient_effects",
        "get_ingredient_regulations",
    }


def test_search_ingredient_devolve_candidatos() -> None:
    conexao = _ConexaoFalsa(fetch_result=[{"ingredient_id": 1, "common_name": "Ácido Hialurônico"}])
    tools = montar_tools_ingrediente(_PoolFalso(conexao))

    resultado = _rodar(_tool(tools, "search_ingredient").ainvoke({"termo": "hialuronico"}))

    assert resultado == [{"ingredient_id": 1, "common_name": "Ácido Hialurônico"}]
    assert conexao.chamadas[0][1] == ("hialuronico",)


def test_get_ingredient_summary_nao_encontrado() -> None:
    conexao = _ConexaoFalsa(fetchrow_result=None)
    tools = montar_tools_ingrediente(_PoolFalso(conexao))

    resultado = _rodar(_tool(tools, "get_ingredient_summary").ainvoke({"ingredient_id": 999}))

    assert resultado == {"erro": "ingrediente não encontrado"}


def test_get_ingredient_effects_sem_profile_tag_passa_none() -> None:
    conexao = _ConexaoFalsa(fetch_result=[])
    tools = montar_tools_ingrediente(_PoolFalso(conexao))

    _rodar(_tool(tools, "get_ingredient_effects").ainvoke({"ingredient_id": 1}))

    assert conexao.chamadas[0][1] == (1, None)


def test_get_ingredient_effects_com_profile_tag() -> None:
    conexao = _ConexaoFalsa(fetch_result=[{"effect_name": "hidratação"}])
    tools = montar_tools_ingrediente(_PoolFalso(conexao))

    resultado = _rodar(
        _tool(tools, "get_ingredient_effects").ainvoke(
            {"ingredient_id": 1, "profile_tag": "pele oleosa"}
        )
    )

    assert resultado == [{"effect_name": "hidratação"}]
    assert conexao.chamadas[0][1] == (1, "pele oleosa")


def test_get_ingredient_regulations_devolve_lista() -> None:
    conexao = _ConexaoFalsa(fetch_result=[{"restriction_type": "proibido", "country": "BR"}])
    tools = montar_tools_ingrediente(_PoolFalso(conexao))

    resultado = _rodar(_tool(tools, "get_ingredient_regulations").ainvoke({"ingredient_id": 1}))

    assert resultado == [{"restriction_type": "proibido", "country": "BR"}]


# --- compartilhada ---


def test_get_user_allergies_devolve_lista() -> None:
    conexao = _ConexaoFalsa(fetch_result=[{"allergy_name": "fragrância", "severity": "alto"}])
    tools = montar_tools_compartilhadas(_PoolFalso(conexao))

    resultado = _rodar(_tool(tools, "get_user_allergies").ainvoke({"user_id": 1}))

    assert resultado == [{"allergy_name": "fragrância", "severity": "alto"}]
    assert conexao.chamadas[0][1] == (1,)
