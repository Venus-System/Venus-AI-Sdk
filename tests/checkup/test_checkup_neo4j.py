"""Check-up contra um Neo4j DE VERDADE (marcados `integration`).

Rodar com um Neo4j de teste VAZIO — a cópia apaga o que não é dela:
    docker run -p 7687:7687 -e NEO4J_AUTH=neo4j/senha-local neo4j:5
    NEO4J_URI=bolt://localhost:7687 NEO4J_PASSWORD=senha-local pytest -m integration tests/checkup
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from _fakes import ConexaoFalsa, PoolFalso
from venus_sdk.checkup.consultas import checar_rotina
from venus_sdk.checkup.sincronizar import sincronizar

neo4j = pytest.importorskip("neo4j")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.getenv("NEO4J_URI"), reason="NEO4J_URI não definida"),
]

_PASTA = Path(__file__).resolve().parents[2] / "data" / "checkup"
# id, nome, categoria, ingredientes (INCI)
_CATALOGO = [
    (1, "Gel de Limpeza", "Gel de Limpeza", ["AQUA"]),
    (2, "Sérum Retinol", "Sérum Facial", ["RETINOL"]),
    (3, "Tônico Glicólico", "Tônico", ["GLYCOLIC ACID"]),
    (4, "Protetor FPS 50", "Protetor Solar", ["ZINC OXIDE"]),
    (5, "Sérum Vitamina C", "Sérum Facial", ["ASCORBIC ACID"]),
    (6, "Outro Sérum Retinol", "Sérum Facial", ["RETINOL"]),
]


def _pool_do_catalogo() -> PoolFalso:
    incis = sorted({inci for *_, ingredientes in _CATALOGO for inci in ingredientes})
    id_do_inci = {inci: 100 + i for i, inci in enumerate(incis)}

    def fetch(query, *args):
        if "FROM venus.products" in query:
            return [{"id": i, "nome": n, "categoria": c} for i, n, c, _ in _CATALOGO]
        if "ingredient_categories" in query:
            return []
        if "FROM venus.ingredients" in query:
            return [{"id": id_do_inci[inci], "inci": inci, "nome": inci.title()} for inci in incis]
        if "product_ingredients" in query:
            return [{"produto": i, "ingrediente": id_do_inci[inci], "posicao": n}
                    for i, _, _, ingredientes in _CATALOGO for n, inci in enumerate(ingredientes, 1)]
        return [{"usuario": 7, "produto": i} for i, *_ in _CATALOGO]

    return PoolFalso(ConexaoFalsa(fetch=fetch))


@pytest.fixture
async def executar():
    driver = neo4j.AsyncGraphDatabase.driver(
        os.environ["NEO4J_URI"], auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD")))

    async def _executar(consulta, parametros):
        async with driver.session() as sessao:
            return await (await sessao.run(consulta, parametros)).data()

    await sincronizar(_pool_do_catalogo(), _executar, _PASTA)
    yield _executar
    await driver.close()


def _passos(*ids: int) -> list[dict]:
    return [{"ordem": ordem, "product_id": i} for ordem, i in enumerate(ids, 1)]


async def test_retinoide_e_acido_na_mesma_noite_e_conflito(executar):
    avisos = await checar_rotina(executar, {"manha": _passos(1, 4), "noite": _passos(1, 2, 3)}, ("noite",))
    conflitos = [a for a in avisos if a["tipo"] == "conflito"]
    assert len(conflitos) == 1 and set(conflitos[0]["ativos"]) == {"Retinoide", "AHA"}


async def test_retinoide_a_noite_e_acido_de_manha_nao_e_conflito(executar):
    avisos = await checar_rotina(executar, {"manha": _passos(3, 4), "noite": _passos(2)}, ("manha", "noite"))
    assert [a for a in avisos if a["tipo"] == "conflito"] == []


async def test_retinoide_sem_protetor_de_manha_falta_filtro_uv(executar):
    avisos = await checar_rotina(executar, {"manha": _passos(1), "noite": _passos(2)}, ("noite",))
    assert [(a["tipo"], a["produto"], a["precisa_de"]) for a in avisos] == [("faltando", "Sérum Retinol", "Filtro UV")]


async def test_protetor_so_a_noite_nao_cobre_o_retinoide(executar):
    avisos = await checar_rotina(executar, {"manha": _passos(1), "noite": _passos(2, 4)}, ("noite",))
    assert any(a["tipo"] == "faltando" for a in avisos)


async def test_protetor_de_manha_cobre_o_retinoide(executar):
    avisos = await checar_rotina(executar, {"manha": _passos(1, 4), "noite": _passos(2)}, ("noite",))
    assert [a for a in avisos if a["tipo"] == "faltando"] == []


async def test_vitamina_c_depois_do_protetor_e_ordem_errada(executar):
    errado = await checar_rotina(executar, {"manha": _passos(4, 5), "noite": []}, ("manha",))
    assert [(a["aplicar_primeiro"], a["aplicar_depois"]) for a in errado if a["tipo"] == "ordem"] == [
        ("Sérum Vitamina C", "Protetor FPS 50")]
    certo = await checar_rotina(executar, {"manha": _passos(5, 4), "noite": []}, ("manha",))
    assert [a for a in certo if a["tipo"] == "ordem"] == []


async def test_dois_serums_de_retinol_sao_repetidos(executar):
    avisos = await checar_rotina(executar, {"manha": _passos(1, 4), "noite": _passos(2, 6)}, ("noite",))
    repetidos = [a for a in avisos if a["tipo"] == "repetido"]
    assert len(repetidos) == 1 and repetidos[0]["semelhanca"] == 1.0


async def test_copia_refeita_nao_duplica(executar):
    await sincronizar(_pool_do_catalogo(), executar, _PASTA)
    [linha] = await executar("MATCH (p:Produto) RETURN count(p) AS total", {})
    assert linha["total"] == len(_CATALOGO)
