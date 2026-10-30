"""Alerta de alergia anexado pelo código depois do Orquestrador: o LLM reescreve
o JSON aprovado pelo Juiz e não pode perder nem suavizar o alerta."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from venus_sdk.nodes.orquestrador import no_orquestrador

_ALERGIAS = [{"allergy_name": "Ácido salicílico"}]
_INGREDIENTES = [
    {"common_name": "Água", "inci_name": "AQUA"},
    {"common_name": "Ácido salicílico", "inci_name": "SALICYLIC ACID"},
]


def _ev(tool: str, resultado, **argumentos) -> dict:
    return {"tool": tool, "resultado": json.dumps(resultado), "argumentos": argumentos}


def _rodar(texto_llm: str, evidencias: list[dict], dominio: str = "produto") -> str:
    estado = {
        "rota": dominio,
        "aprovado_juiz": True,
        "pergunta_original": "esse sérum serve pra mim?",
        "evidencias_tools": evidencias,
        "resposta_especialista": {"dominio": dominio, "intencao": "informar", "recomendacao": "",
                                  "resposta": texto_llm, "fontes_usadas": ["get_product_ingredients"]},
    }
    with patch("venus_sdk.nodes.orquestrador.get_llm_orquestrador") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = SimpleNamespace(content=texto_llm)
        return no_orquestrador(estado)["resposta_final"]


def _evidencias_do_produto(alergias=_ALERGIAS) -> list[dict]:
    return [
        _ev("search_product", [{"product_id": 7, "name": "Sérum Clareador", "brand_name": "Marca"}]),
        _ev("get_product_ingredients", _INGREDIENTES, product_id=7),
        _ev("get_user_allergies", alergias),
    ]


def test_anexa_o_alerta_quando_o_llm_nao_avisa() -> None:
    texto = _rodar("O Sérum Clareador é ótimo para manchas, pode usar tranquila.", _evidencias_do_produto())
    assert texto.startswith("O Sérum Clareador é ótimo")
    assert "Atenção: Sérum Clareador tem Ácido salicílico, que bate com a sua alergia a Ácido salicílico" in texto


def test_nao_duplica_o_alerta_que_ja_esta_no_texto() -> None:
    alerta = ("Atenção: Sérum Clareador tem Ácido salicílico, que bate com a sua alergia a Ácido salicílico "
              "(conferido no seu cadastro).")
    texto = _rodar(f"Sobre o Sérum Clareador.\n\n{alerta}", _evidencias_do_produto())
    assert texto.count("Atenção:") == 1


def test_sem_alergia_declarada_nao_anexa_nada() -> None:
    texto = _rodar("O Sérum Clareador é ótimo para manchas.",
                   _evidencias_do_produto(alergias={"encontrado": False, "alergias": []}))
    assert "Atenção" not in texto


def test_produto_sem_conflito_nao_recebe_alerta() -> None:
    texto = _rodar("O Sérum Clareador é ótimo para manchas.",
                   _evidencias_do_produto(alergias=[{"allergy_name": "Fragrância (Parfum)"}]))
    assert "Atenção" not in texto


def test_produto_secundario_so_recebe_alerta_se_o_texto_cita() -> None:
    evidencias = [
        _ev("search_product", [{"product_id": 7, "name": "Sérum Clareador", "brand_name": "Marca"},
                               {"product_id": 8, "name": "Tônico Esfoliante", "brand_name": "Marca"}]),
        _ev("get_product_ingredients", [{"common_name": "Água", "inci_name": "AQUA"}], product_id=7),
        _ev("get_product_ingredients", _INGREDIENTES, product_id=8),
        _ev("get_user_allergies", _ALERGIAS),
    ]
    assert "Atenção" not in _rodar("O Sérum Clareador não tem nada da sua alergia.", evidencias)
    citado = _rodar("O Sérum Clareador é melhor que o Tônico Esfoliante.", evidencias)
    assert "Atenção: Tônico Esfoliante tem Ácido salicílico" in citado


def test_ingrediente_buscado_que_bate_com_a_alergia_recebe_alerta() -> None:
    evidencias = [
        _ev("search_ingredient", [{"ingredient_id": 1, "common_name": "Ácido salicílico", "inci_name": "SALICYLIC ACID"}]),
        _ev("get_user_allergies", _ALERGIAS),
    ]
    texto = _rodar("O ácido salicílico é um esfoliante químico, ótimo para acne.", evidencias, dominio="ingrediente")
    assert "Atenção: Ácido salicílico bate com a sua alergia a Ácido salicílico" in texto
