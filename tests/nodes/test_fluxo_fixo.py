"""Fluxo fixo de Produto e Ingrediente: acha o item sem LLM ou devolve `None` para o agente."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from venus_sdk.nodes.fluxo_fixo import (
    evidencias_de_faq,
    evidencias_de_ingrediente,
    evidencias_de_produto,
)


class ToolFalsa:
    def __init__(self, nome: str, retorno: Any) -> None:
        self.name, self.retorno, self.chamadas = nome, retorno, []

    async def ainvoke(self, argumentos: dict) -> Any:
        self.chamadas.append(argumentos)
        return self.retorno(argumentos) if callable(self.retorno) else self.retorno


NOMES = ("get_product", "get_product_score", "get_personalized_score", "get_product_ingredients",
         "get_user_allergies", "get_user_profile", "get_ingredient_summary", "get_ingredient_properties",
         "get_ingredient_effects", "get_ingredient_regulations")


def _tools(**retornos: Any) -> dict[str, ToolFalsa]:
    tools = {nome: ToolFalsa(nome, {"encontrado": False}) for nome in NOMES}
    tools.update({nome: ToolFalsa(nome, retorno) for nome, retorno in retornos.items()})
    return tools


SERUM_X = {"product_id": 5, "name": "Sérum Retinol X", "brand_name": "M", "relevancia": 3}
SERUM_Y = {"product_id": 6, "name": "Sérum Vitamina Y", "brand_name": "M", "relevancia": 1}


def _nomes(saida) -> list[str]:
    return [e["tool"] for e in saida[0]]


async def test_produto_citado_traz_a_ficha_e_o_usuario():
    saida = await evidencias_de_produto("qual a nota do Sérum Retinol X?", None, 1, _tools(search_product=[SERUM_Y, SERUM_X]))
    assert saida[1] == "informar"
    assert {"search_product", "get_product_score", "get_product_ingredients", "get_user_allergies"} <= set(_nomes(saida))
    assert all(e["argumentos"].get("product_id", 5) == 5 for e in saida[0])


async def test_empate_na_busca_vai_para_o_agente():
    empate = [{**SERUM_X, "relevancia": 1}, SERUM_Y]
    assert await evidencias_de_produto("qual a nota do sérum?", None, 1, _tools(search_product=empate)) is None


async def test_referencia_sem_produto_em_tela_vai_para_o_agente():
    assert await evidencias_de_produto("quais os ingredientes dele?", None, 1, _tools(search_product=[SERUM_X])) is None


async def test_referencia_com_produto_em_tela_busca_o_produto_da_tela():
    tools = _tools(search_product=[SERUM_X])
    saida = await evidencias_de_produto("quais os ingredientes dele?", "Sérum Retinol X", 1, tools)
    assert tools["search_product"].chamadas == [{"termo": "Sérum Retinol X"}] and saida[1] == "informar"


async def test_sugestao_consulta_varios_e_nao_escolhe_um():
    saida = await evidencias_de_produto("me indica um sérum", None, 1, _tools(search_product=[SERUM_X, SERUM_Y]))
    assert saida[1] == "sugerir" and _nomes(saida).count("get_product_ingredients") == 2


async def test_nada_achado_vai_para_o_agente():
    assert await evidencias_de_produto("qual a nota do Zorblax?", None, 1, _tools(search_product={"encontrado": False})) is None


async def test_sem_usuario_nao_consulta_dados_da_conta():
    saida = await evidencias_de_produto("qual a nota do Sérum Retinol X?", None, None, _tools(search_product=[SERUM_X]))
    assert not {"get_user_allergies", "get_user_profile", "get_personalized_score"} & set(_nomes(saida))


async def test_ingrediente_achado_pelo_trecho_mais_longo():
    def busca(argumentos):
        return [{"ingredient_id": 9, "common_name": "Ácido hialurônico"}] if argumentos["termo"] == "acido hialuronico" else {"encontrado": False}

    tools = _tools(search_ingredient=busca)
    saida = await evidencias_de_ingrediente("pra que serve o ácido hialurônico?", 1, tools)
    assert "get_ingredient_effects" in _nomes(saida)


async def test_varios_ingredientes_nao_escolhe_um():
    achados = [{"ingredient_id": 1, "common_name": "A"}, {"ingredient_id": 2, "common_name": "B"}]
    saida = await evidencias_de_ingrediente("o que é retinol?", None, _tools(search_ingredient=achados))
    assert _nomes(saida) == ["search_ingredient"]


async def test_calculo_vai_para_o_agente():
    assert await evidencias_de_ingrediente("qual o limite de 2% de ácido salicílico?", 1, _tools()) is None


TRECHOS = [{"trecho": "Limites > X\n\nOutro assunto.", "fonte": "limites.md"},
           {"trecho": "Privacidade > Vende?\n\nNão. O Venus não vende.", "fonte": "privacidade.md"}]


async def _faq(saida_llm: str, web: Any = None):
    tools = _tools(faq_retriever=TRECHOS, buscar_na_web=web or {"encontrado": False})
    with patch("venus_sdk.nodes.fluxo_fixo.get_llm_rapido") as get_llm:
        get_llm.return_value.invoke.return_value = SimpleNamespace(content=saida_llm)
        return await evidencias_de_faq("vocês vendem meus dados?", tools), tools


async def test_faq_mostra_o_trecho_escolhido_sem_o_caminho_do_titulo():
    (evidencias, fontes), tools = await _faq("2")
    assert fontes == [{"texto": "Não. O Venus não vende.", "fonte": "privacidade.md"}]
    assert [e["tool"] for e in evidencias] == ["faq_retriever"]


async def test_faq_sem_trecho_nao_vai_para_a_web():
    (_, fontes), tools = await _faq("NENHUM", [{"trecho": "Da web.", "url": "https://x.org"}])
    assert fontes == [] and tools["buscar_na_web"].chamadas == []


async def test_numero_fora_da_lista_nao_escolhe_trecho():
    (_, fontes), _ = await _faq("7")
    assert fontes == []
