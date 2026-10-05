"""Item 7 da revisão técnica 3: contas determinísticas (concentração, unidade,
datas) feitas por tools, nunca de cabeça pelo LLM."""

import asyncio
import json
import math
from datetime import date

import pytest
from langchain_core.messages import AIMessage

from _fakes import LLMScript, PoolFalso, ConexaoFalsa, chamada_tool
from venus_sdk.tools import calculos

# --- converter_concentracao -----------------------------------------------------


@pytest.mark.parametrize(("valor", "de", "para", "esperado"), [
    (2, "%", "mg/g", 20),
    (2, "%", "ppm", 20000),
    (20, "mg/g", "%", 2),
    (500, "ppm", "%", 0.05),
    (1, "mg/g", "ppm", 1000),
    (0.5, "g/100g", "mg/kg", 5000),     # apelidos: g/100g = %, mg/kg = ppm
    (3, "%", "%", 3),
    (0, "%", "ppm", 0),
])
def test_converte_entre_unidades(valor, de, para, esperado):
    resultado = calculos.converter_concentracao(valor, de, para)
    assert math.isclose(resultado["resultado"], esperado)
    assert resultado["unidade"] and resultado["formula"]


def test_ida_e_volta_preserva_o_valor():
    ida = calculos.converter_concentracao(0.75, "%", "ppm")["resultado"]
    assert math.isclose(calculos.converter_concentracao(ida, "ppm", "%")["resultado"], 0.75)


@pytest.mark.parametrize("unidade", ["mg", "ml", "", "porcento", "kg/g"])
def test_unidade_desconhecida_e_recusada(unidade):
    with pytest.raises(ValueError, match="unidade"):
        calculos.converter_concentracao(1, unidade, "%")


@pytest.mark.parametrize("valor", [-1, float("nan"), float("inf"), True, "2"])
def test_valor_invalido_e_recusado(valor):
    with pytest.raises(ValueError):
        calculos.converter_concentracao(valor, "%", "ppm")


def test_porcentagem_acima_de_100_e_recusada():
    with pytest.raises(ValueError, match="100"):
        calculos.converter_concentracao(150, "%", "mg/g")


# --- comparar_com_limite ----------------------------------------------------------


def test_acima_do_limite():
    resultado = calculos.comparar_com_limite(2, "%", 1, "%")
    assert resultado["dentro_do_limite"] is False and resultado["resultado"] == 2
    assert math.isclose(resultado["diferenca_para_o_limite"], -1)


def test_dentro_do_limite_com_unidades_diferentes():
    resultado = calculos.comparar_com_limite(5, "mg/g", 1, "%")  # 5 mg/g = 0,5 %
    assert resultado["dentro_do_limite"] is True
    assert math.isclose(resultado["resultado"], 0.5) and resultado["unidade"] == "%"


def test_igual_ao_limite_esta_dentro():
    assert calculos.comparar_com_limite(10000, "ppm", 1, "%")["dentro_do_limite"] is True


def test_limite_invalido_e_recusado():
    with pytest.raises(ValueError):
        calculos.comparar_com_limite(1, "%", -2, "%")


# --- datas --------------------------------------------------------------------------


@pytest.mark.parametrize(("inicio", "fim", "dias", "semanas", "resto"), [
    (date(2026, 10, 1), date(2026, 10, 1), 0, 0, 0),
    (date(2026, 9, 1), date(2026, 10, 4), 33, 4, 5),
    (date(2025, 12, 30), date(2026, 1, 6), 7, 1, 0),   # vira o ano
    (date(2024, 2, 28), date(2024, 3, 1), 2, 0, 2),    # ano bissexto
])
def test_tempo_entre_datas(inicio, fim, dias, semanas, resto):
    resultado = calculos.tempo_entre_datas(inicio, fim)
    assert (resultado["resultado"], resultado["semanas_completas"], resultado["dias_alem_das_semanas"]) == (dias, semanas, resto)


def test_inicio_depois_do_fim_e_recusado():
    with pytest.raises(ValueError, match="depois"):
        calculos.tempo_entre_datas(date(2026, 10, 5), date(2026, 10, 4))


def test_datas_em_dias_alternados():
    resultado = calculos.datas_de_aplicacao(date(2026, 12, 30), intervalo_dias=2, quantidade=4)
    assert resultado["datas"] == ["2026-12-30", "2027-01-01", "2027-01-03", "2027-01-05"]
    assert resultado["resultado"] == 4


@pytest.mark.parametrize(("intervalo", "quantidade"), [(0, 3), (15, 3), (2, 0), (2, 61), (1.5, 3)])
def test_datas_de_aplicacao_recusa_parametros_fora_da_faixa(intervalo, quantidade):
    with pytest.raises(ValueError):
        calculos.datas_de_aplicacao(date(2026, 10, 1), intervalo_dias=intervalo, quantidade=quantidade)


# --- tools ---------------------------------------------------------------------------


def _tools(montar):
    return {t.name: t for t in montar()}


def test_tools_devolvem_resultado_e_formula():
    tools = _tools(calculos.montar_tools_calculo_ingrediente)
    saida = tools["comparar_concentracao_com_limite"].invoke(
        {"valor": 2, "unidade": "%", "limite": 1, "unidade_limite": "%"})
    assert saida["dentro_do_limite"] is False and "formula" in saida and "valor_para_citar" in saida


def test_tool_com_entrada_invalida_devolve_erro_sem_levantar():
    tools = _tools(calculos.montar_tools_calculo_ingrediente)
    saida = tools["converter_concentracao"].invoke({"valor": 1, "de": "colher", "para": "%"})
    assert "erro" in saida


def test_tool_de_tempo_de_uso_usa_a_data_de_hoje(monkeypatch):
    monkeypatch.setattr(calculos, "_hoje", lambda: date(2026, 10, 4))
    tools = _tools(calculos.montar_tools_calculo_rotina)
    saida = tools["calcular_tempo_de_uso"].invoke({"data_inicio": "2026-09-01"})
    assert saida["resultado"] == 33
    assert "erro" in tools["calcular_tempo_de_uso"].invoke({"data_inicio": "01/09/2026"})


def test_nao_existe_calculadora_de_expressao_livre():
    for montar in (calculos.montar_tools_calculo_ingrediente, calculos.montar_tools_calculo_rotina):
        for tool in montar():
            assert not ({"expressao", "expression", "formula"} & set(tool.args))


def test_agentes_certos_recebem_as_tools():
    from venus_sdk.nodes import especialistas as esp

    assert {"converter_concentracao", "comparar_concentracao_com_limite"} <= set(esp.TOOLS_DE_CALCULO_POR_AGENTE["ingrediente"])
    assert {"calcular_tempo_de_uso", "calcular_datas_de_aplicacao"} <= set(esp.TOOLS_DE_CALCULO_POR_AGENTE["rotina"])


def test_prompts_mandam_usar_a_tool_de_calculo():
    from venus_sdk.prompts.ingrediente import ESP_INGREDIENTE_PROMPT_COMPLETO
    from venus_sdk.prompts.rotina import ROTINA_PROMPT_COMPLETO

    for prompt in (ESP_INGREDIENTE_PROMPT_COMPLETO, ROTINA_PROMPT_COMPLETO):
        assert "nunca calcule de cabeça" in prompt


# --- especialista com LLM falso -------------------------------------------------------


def test_especialista_de_ingrediente_chama_a_tool_e_usa_o_numero_dela(monkeypatch):
    from venus_sdk.nodes import especialistas as esp

    def responder_com_o_numero(mensagens):
        retorno = json.loads(mensagens[-1].content)
        return AIMessage(content=json.dumps({
            "dominio": "ingrediente", "intencao": "consultar",
            "resposta": f"Em mg/g, 0,5% equivale a {retorno['valor_para_citar']}.",
            "recomendacao": "", "fontes_usadas": ["converter_concentracao"],
        }, ensure_ascii=False))

    llm = LLMScript(script=[chamada_tool("converter_concentracao", {"valor": 0.5, "de": "%", "para": "mg/g"}),
                            responder_com_o_numero])
    llm.chamadas = []
    monkeypatch.setattr(esp, "get_llm_especialista", lambda: llm)
    no = esp.montar_no_agente_ingrediente(PoolFalso(ConexaoFalsa()))
    saida = asyncio.run(no({"pergunta_original": "quanto é 0,5% em mg/g?", "mensagem_usuario": "quanto é 0,5% em mg/g?"}))

    evidencia = next(e for e in saida["evidencias_tools"] if e["tool"] == "converter_concentracao")
    numero = json.loads(evidencia["resultado"])["valor_para_citar"]
    assert numero == "5 mg/g" and numero in saida["resposta_especialista"]["resposta"]


# --- juiz -------------------------------------------------------------------------------


def _estado_do_juiz(resposta: str, valor_para_citar: str = "20 mg/g", resultado: float = 20) -> dict:
    return {
        "pergunta_original": "2% em mg/g?",
        "resposta_especialista": {"dominio": "ingrediente", "intencao": "consultar", "resposta": resposta,
                                  "recomendacao": "", "fontes_usadas": ["converter_concentracao"]},
        "evidencias_tools": [{"tool": "converter_concentracao",
                              "resultado": json.dumps({"resultado": resultado, "unidade": "mg/g",
                                                       "valor_para_citar": valor_para_citar,
                                                       "formula": "2 % × 10 = 20 mg/g"}),
                              "argumentos": {"valor": 2, "de": "%", "para": "mg/g"}}],
    }


class _JuizQueAprova:
    def __init__(self):
        self.chamadas = 0

    def invoke(self, mensagens):
        self.chamadas += 1
        return AIMessage(content="RESULTADO=aprovado")


def test_juiz_reprova_numero_diferente_do_calculado_sem_chamar_o_llm(monkeypatch):
    from venus_sdk.nodes import juiz

    llm = _JuizQueAprova()
    monkeypatch.setattr(juiz, "get_llm_juiz", lambda: llm)
    saida = juiz.no_agente_juiz(_estado_do_juiz("2% equivale a 200 mg/g."))
    assert saida["aprovado_juiz"] is False and "20 mg/g" in saida["feedback_juiz"]
    assert llm.chamadas == 0


@pytest.mark.parametrize("resposta", ["2% equivale a 20 mg/g.", "Isso dá 20,0 mg/g."])
def test_juiz_segue_para_o_llm_quando_o_numero_bate(monkeypatch, resposta):
    from venus_sdk.nodes import juiz

    llm = _JuizQueAprova()
    monkeypatch.setattr(juiz, "get_llm_juiz", lambda: llm)
    assert juiz.no_agente_juiz(_estado_do_juiz(resposta))["aprovado_juiz"] is True
    assert llm.chamadas == 1


def test_juiz_aceita_numero_com_separador_de_milhar(monkeypatch):
    from venus_sdk.nodes import juiz

    monkeypatch.setattr(juiz, "get_llm_juiz", lambda: _JuizQueAprova())
    estado = _estado_do_juiz("2% são 20.000 ppm.", valor_para_citar="20000 ppm", resultado=20000)
    assert juiz.no_agente_juiz(estado)["aprovado_juiz"] is True
