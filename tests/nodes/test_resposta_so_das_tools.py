"""`VENUS_RESPOSTA_SO_DAS_TOOLS`: nessas rotas, nenhum texto de LLM chega ao usuário."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from venus_sdk.nodes.juiz import no_agente_juiz
from venus_sdk.nodes.orquestrador import no_orquestrador


def _ev(tool, resultado, **argumentos):
    return {"tool": tool, "resultado": json.dumps(resultado), "argumentos": argumentos}


EVIDENCIAS = [
    _ev("search_product", [{"product_id": 5, "name": "Sérum X", "brand_name": "Marca"}]),
    _ev("get_product_score", {"overall_score": 58}, product_id=5),
    _ev("get_product_ingredients", [{"common_name": "Água", "inci_name": "AQUA"},
                                    {"common_name": "Fragrância", "inci_name": "PARFUM"}], product_id=5),
]


def _responder(pergunta: str, evidencias=None) -> str:
    estado = {"rota": "produto", "aprovado_juiz": True, "pergunta_original": pergunta,
              "evidencias_tools": EVIDENCIAS if evidencias is None else evidencias,
              "resposta_especialista": {"dominio": "produto", "intencao": "informar",
                                        "resposta": "Ajuda a tirar rugas em 4 semanas.", "recomendacao": ""}}
    with patch("venus_sdk.nodes.orquestrador.get_llm_orquestrador", side_effect=AssertionError("sem LLM")):
        return no_orquestrador(estado)["resposta_final"]


@pytest.fixture(autouse=True)
def _ligado(monkeypatch):
    monkeypatch.setenv("VENUS_RESPOSTA_SO_DAS_TOOLS", "produto,ingrediente")


def test_texto_do_especialista_nao_chega_ao_usuario():
    assert "rugas" not in _responder("qual a nota do Sérum X?")


def test_responde_a_nota_com_o_dado_da_tool():
    assert "Nota geral: 58/100" in _responder("qual a nota do Sérum X?")


def test_ingrediente_presente_e_ausente():
    assert _responder("o Sérum X tem fragrância?").startswith("Sim: Fragrância")
    assert _responder("o Sérum X tem silicone?").startswith("Não: silicone não aparece nos 2")


def test_pergunta_sem_dado_diz_que_nao_tem():
    assert _responder("em quantas semanas o Sérum X tira rugas?").startswith("Não tenho um dado cadastrado")


def test_juiz_nao_chama_o_llm_nem_reprova():
    estado = {"rota": "produto", "resposta_especialista": {"dominio": "produto", "intencao": "informar",
                                                           "resposta": "sem fragrância", "recomendacao": ""}}
    with patch("venus_sdk.nodes.juiz.get_llm_juiz", side_effect=AssertionError("sem LLM")):
        assert no_agente_juiz(estado)["aprovado_juiz"] is True


def test_rota_fora_da_lista_nao_muda(monkeypatch):
    monkeypatch.setenv("VENUS_RESPOSTA_SO_DAS_TOOLS", "ingrediente")
    with patch("venus_sdk.nodes.orquestrador.get_llm_orquestrador") as get_llm:
        get_llm.return_value.invoke.return_value.content = "Texto do LLM com a resposta completa."
        estado = {"rota": "produto", "aprovado_juiz": True, "pergunta_original": "oi",
                  "resposta_especialista": {"dominio": "produto", "resposta": "x", "recomendacao": ""}}
        assert no_orquestrador(estado)["resposta_final"].startswith("Texto do LLM")


def test_padrao_cobre_produto_e_ingrediente_e_nao_rotina_nem_faq(monkeypatch):
    from venus_sdk.nodes._evidencias import rota_so_com_tools

    monkeypatch.delenv("VENUS_RESPOSTA_SO_DAS_TOOLS")
    assert rota_so_com_tools("produto") and rota_so_com_tools("ingrediente")
    assert not rota_so_com_tools("rotina") and not rota_so_com_tools("faq") and not rota_so_com_tools(None)


def test_pergunta_de_produto_nao_vai_para_ingrediente_por_causa_do_nome():
    from venus_sdk.nodes.roteador import _rota_obrigatoria

    assert _rota_obrigatoria("ingrediente", "o Sérum Hidratante com Ácido Hialurônico tem fragrância?") == "produto"
    assert _rota_obrigatoria("ingrediente", "o ácido hialurônico faz mal?") is None


def test_rota_inferida_por_palavras_tambem_respeita_a_pergunta_de_produto():
    from venus_sdk.nodes.roteador import _aplicar_redes_de_seguranca

    rota, _ = _aplicar_redes_de_seguranca(None, "texto direto", "o CeraVe Hidratante Noite tem fragrância?")
    assert rota == "produto"
