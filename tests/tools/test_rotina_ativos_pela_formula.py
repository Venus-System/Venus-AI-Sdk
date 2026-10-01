"""Rotina: o horário de cada produto é decidido pela fórmula (e pelo nome
só quando não há ingredientes cadastrados)."""

from __future__ import annotations

import pytest

from venus_sdk.tools import rotina


def _produto(nome: str, ingredientes: list[str], categoria: str = "Hidratante") -> dict:
    return {"product_id": 1, "name": nome, "category_name": categoria, "inci_names": ingredientes}


@pytest.mark.parametrize(("ingrediente", "ativo"), [
    ("glycolic acid", "glycolic acid"),
    ("salicylic acid", "salicylic acid"),
    ("capryloyl salicylic acid", "salicylic acid"),
    ("lactic acid", "lactic acid"),
    ("retinol", "retinol"),
    ("ácido glicólico", "acido glicolico"),
])
def test_ativo_na_formula_tira_da_manha_mesmo_sem_estar_no_nome(ingrediente, ativo):
    produto = _produto("CeraVe Acne Control Cuidado Diário", ["aqua", ingrediente])
    motivo = rotina._motivo_fora_do_horario(produto, "manha")
    assert motivo and ativo in motivo
    assert rotina._serve_no_horario(produto, "noite") is True


@pytest.mark.parametrize("ingrediente", ["hyaluronic acid", "thioglycolic acid", "ascorbic acid", "retinyl palmitate"])
def test_ingrediente_parecido_nao_conta(ingrediente):
    assert rotina._motivo_fora_do_horario(_produto("Sérum X", [ingrediente]), "manha") is None


def test_sem_ingredientes_cadastrados_vale_o_nome():
    produto = _produto("CeraVe Sérum Retinol Refinador", [], categoria="Sérum Facial")
    assert "nome" in rotina._motivo_fora_do_horario(produto, "manha")


def test_rotina_explica_o_que_ficou_fora_do_horario():
    favoritos = [
        {"product_id": 1, "name": "Gel de Limpeza", "category_name": "Gel de Limpeza", "inci_names": ["aqua"]},
        {"product_id": 2, "name": "Cuidado Diário", "category_name": "Hidratante", "inci_names": ["glycolic acid"]},
    ]
    resultado = rotina._montar_rotina(favoritos, [], "manha")
    assert [p["product_id"] for p in resultado["passos"]] == [1]
    assert resultado["fora_do_horario"] == [
        {"product_id": 2, "nome": "Cuidado Diário", "motivo": "contém glycolic acid, que deixa a pele sensível ao sol"}
    ]
