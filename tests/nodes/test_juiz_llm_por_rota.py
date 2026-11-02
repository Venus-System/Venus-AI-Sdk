"""`VENUS_JUIZ_LLM_ROTAS` escolhe em quais rotas o LLM do Juiz roda; as regras em
código valem em todas."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from venus_sdk.nodes.juiz import no_agente_juiz


def _estado(rota: str, resposta: str = "Tem hidratação.") -> dict:
    return {
        "rota": rota,
        "resposta_especialista": {"dominio": rota, "intencao": "informar", "resposta": resposta, "recomendacao": "",
                                  "fontes_usadas": ["get_ingredient_summary"]},
        "evidencias_tools": [{"tool": "get_ingredient_summary", "resultado": json.dumps({"function_summary": "x"})}],
    }


def _julgar(estado: dict) -> tuple[dict, object]:
    with patch("venus_sdk.nodes.juiz.get_llm_juiz") as get_llm:
        get_llm.return_value.invoke.return_value = SimpleNamespace(content="RESULTADO=[REPROVADO]\nFEEDBACK=[inventou]")
        return no_agente_juiz(estado), get_llm


def test_sem_a_variavel_o_llm_roda_em_toda_rota(monkeypatch) -> None:
    monkeypatch.delenv("VENUS_JUIZ_LLM_ROTAS", raising=False)
    veredito, get_llm = _julgar(_estado("ingrediente"))
    get_llm.return_value.invoke.assert_called_once()
    assert veredito["juiz_decidiu_por"] == "llm"


def test_rota_fora_da_lista_nao_chama_o_llm(monkeypatch) -> None:
    monkeypatch.setenv("VENUS_JUIZ_LLM_ROTAS", "rotina,faq")
    veredito, get_llm = _julgar(_estado("ingrediente"))
    get_llm.assert_not_called()
    assert veredito["aprovado_juiz"] is True and veredito["juiz_decidiu_por"] == "regra"


def test_rota_na_lista_continua_com_o_llm(monkeypatch) -> None:
    monkeypatch.setenv("VENUS_JUIZ_LLM_ROTAS", "rotina,faq")
    veredito, get_llm = _julgar(_estado("faq"))
    get_llm.return_value.invoke.assert_called_once()
    assert veredito["aprovado_juiz"] is False


def test_regra_em_codigo_reprova_mesmo_sem_o_llm(monkeypatch) -> None:
    monkeypatch.setenv("VENUS_JUIZ_LLM_ROTAS", "rotina,faq")
    estado = _estado("produto", "O Sérum X não tem nada da sua alergia, sem fragrância.")
    estado["resposta_especialista"].update(dominio="produto", produtos=[{"product_id": 7, "nome": "Sérum X"}])
    veredito, get_llm = _julgar(estado)
    get_llm.assert_not_called()
    assert veredito["aprovado_juiz"] is False
