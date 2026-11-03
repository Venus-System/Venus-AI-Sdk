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
    monkeypatch.setenv("VENUS_RESPOSTA_SO_DAS_TOOLS", "produto,ingrediente,rotina,faq")


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


def test_padrao_cobre_todas_as_rotas_com_especialista(monkeypatch):
    from venus_sdk.nodes._evidencias import rota_so_com_tools

    monkeypatch.delenv("VENUS_RESPOSTA_SO_DAS_TOOLS")
    assert all(rota_so_com_tools(rota) for rota in ("produto", "ingrediente", "rotina", "faq"))
    assert not rota_so_com_tools(None)


def test_pergunta_de_produto_nao_vai_para_ingrediente_por_causa_do_nome():
    from venus_sdk.nodes.roteador import _rota_obrigatoria

    assert _rota_obrigatoria("ingrediente", "o Sérum Hidratante com Ácido Hialurônico tem fragrância?") == "produto"
    assert _rota_obrigatoria("ingrediente", "o ácido hialurônico faz mal?") is None


def test_rota_inferida_por_palavras_tambem_respeita_a_pergunta_de_produto():
    from venus_sdk.nodes.roteador import _aplicar_redes_de_seguranca

    rota, _ = _aplicar_redes_de_seguranca(None, "texto direto", "o CeraVe Hidratante Noite tem fragrância?")
    assert rota == "produto"


def _estado_rotina(evidencias, **extra):
    return {"rota": "rotina", "aprovado_juiz": True, "pergunta_original": "monta minha rotina",
            "evidencias_tools": evidencias,
            "resposta_especialista": {"dominio": "rotina", "intencao": "criar", "resposta": "Espere 3 minutos entre os passos.",
                                      "recomendacao": ""}, **extra}


def test_rotina_sai_das_tools_sem_o_texto_do_llm(monkeypatch):
    monkeypatch.setenv("VENUS_RESPOSTA_SO_DAS_TOOLS", "rotina")
    evidencias = [
        _ev("suggest_routine", {"passos": [{"ordem": 1, "nome": "Gel X"}]}),
        _ev("check_routine_health", {"checado": True, "avisos": [{"motivo": "retinoide pede protetor de manhã"}]}),
        _ev("calcular_tempo_de_uso", {"valor_para_citar": "12 dias"}),
    ]
    with patch("venus_sdk.nodes.orquestrador.get_llm_orquestrador", side_effect=AssertionError("sem LLM")):
        texto = no_orquestrador(_estado_rotina(evidencias))["resposta_final"]
    assert "1) Gel X" in texto and "retinoide pede protetor" in texto and "12 dias" in texto
    assert "3 minutos" not in texto and "Não tenho um dado" not in texto


def test_rotina_mantem_o_pedido_de_confirmacao_do_agendamento(monkeypatch):
    monkeypatch.setenv("VENUS_RESPOSTA_SO_DAS_TOOLS", "rotina")
    proposta = [{"resumo": "Rotina às 22h", "acao": "criar", "horario": "22:00", "periodo": "noite"}]
    with patch("venus_sdk.nodes.orquestrador._pedido_de_confirmacao", return_value="Posso agendar?") as pedido:
        texto = no_orquestrador(_estado_rotina([_ev("suggest_routine", {"passos": [{"ordem": 1, "nome": "Gel X"}]})],
                                                agendamento_pendente=proposta))["resposta_final"]
    pedido.assert_called_once_with(proposta)
    assert texto.endswith("Posso agendar?")


def _estado_faq(fontes):
    return {"rota": "faq", "aprovado_juiz": True, "pergunta_original": "vocês vendem meus dados?", "evidencias_tools": [],
            "resposta_especialista": {"dominio": "faq", "intencao": "informar", "resposta": "", "fontes_a_mostrar": fontes}}


def test_faq_mostra_o_documento_com_a_fonte():
    texto = no_orquestrador(_estado_faq([{"texto": "Não. O Venus não vende.", "fonte": "privacidade.md"}]))["resposta_final"]
    assert texto.endswith("Não. O Venus não vende.\n\nFonte: privacidade.md")


def test_faq_sem_fonte_diz_que_nao_encontrou():
    assert no_orquestrador(_estado_faq([]))["resposta_final"] == "Não encontrei isso na documentação do Venus."


def test_pergunta_de_saude_recebe_orientacao_fixa_e_nao_o_texto_do_llm():
    estado = {"rota": "ingrediente", "aprovado_juiz": True, "pergunta_original": "retinol pode ser usado na gravidez?",
              "evidencias_tools": [_ev("search_ingredient", [{"ingredient_id": 1, "common_name": "RETINOL"}])],
              "resposta_especialista": {"dominio": "ingrediente", "intencao": "informar", "resposta": "invento"}}
    texto = no_orquestrador(estado)["resposta_final"]
    assert texto.startswith("Em gravidez, amamentação ou reação na pele, converse com um profissional de saúde.")
    assert "Não tenho um dado cadastrado" in texto and "invento" not in texto


def test_sugestao_avisa_que_a_busca_e_por_palavras():
    estado = {"rota": "produto", "aprovado_juiz": True, "pergunta_original": "me indica um protetor pra pele oleosa",
              "evidencias_tools": EVIDENCIAS,
              "resposta_especialista": {"dominio": "produto", "intencao": "sugerir", "resposta": "invento"}}
    texto = no_orquestrador(estado)["resposta_final"]
    assert texto.startswith("Busquei as palavras do seu pedido") and "Sérum X" in texto and "invento" not in texto


def test_pergunta_sobre_um_produto_nao_recebe_o_aviso_da_busca():
    assert "Busquei as palavras" not in _responder("qual a nota do Sérum X?")
