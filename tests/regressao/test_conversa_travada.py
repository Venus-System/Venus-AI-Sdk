"""Correções da conversa real de 2026-10-20 que "travou" repetindo a mesma ficha
do CeraVe Creme Hidratante: prefixo "Sobre <produto>:" forçando a rota produto,
especialista/Juiz sem histórico, falso positivo da alergia a "Álcool" (álcool
graxo), Juiz reprovando pedido de esclarecimento, fallback que não explicava a
rotina vazia, memória que nunca gravava e produto duplicado na busca."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from _fakes import LLMScript
from langchain_core.messages import AIMessage, HumanMessage

from venus_sdk.alergias import TERMO_ALCOOL_ETILICO, termo_bate, termos_da_alergia
from venus_sdk.nodes._historico import historico_recente
from venus_sdk.nodes.especialistas import _montar_entrada
from venus_sdk.nodes.juiz import no_agente_juiz
from venus_sdk.nodes.memoria import _extrair_fatos_novos
from venus_sdk.nodes.orquestrador import _resposta_segura_sem_aprovacao
from venus_sdk.nodes.roteador import (
    decidir_especialista,
    e_small_talk,
    no_roteador,
    separar_produto_em_contexto,
)
from venus_sdk.tools.produto import _sem_duplicatas
from venus_sdk.tools.rotina import _montar_rotina, _termos_da_alergia

PREFIXO = "Sobre CeraVe Creme Hidratante: "


# --- prefixo "Sobre <produto>:" ---


def test_separar_produto_em_contexto() -> None:
    assert separar_produto_em_contexto(PREFIXO + "sim, sugira") == ("CeraVe Creme Hidratante", "sim, sugira")
    assert separar_produto_em_contexto("monta minha rotina") == (None, "monta minha rotina")


def test_pergunta_sobre_a_conversa_com_produto_na_tela_nao_e_forcada_para_produto() -> None:
    llm = LLMScript(script=[AIMessage(content="Você disse que eu tinha contraindicado esse produto!")])
    with patch("venus_sdk.nodes.roteador.get_llm_rapido", return_value=llm):
        estado = no_roteador({"mensagem_usuario": PREFIXO + "oq eu acabei de falar no prompt anterior?",
                              "historico": []})
    assert decidir_especialista(estado) == "direto"
    assert "contraindicado" in estado["resposta_final"]
    # O LLM roteador recebe o produto como contexto separado, não colado na pergunta.
    assert "PRODUTO_EM_TELA=CeraVe Creme Hidratante\noq eu acabei" in llm.chamadas[-1][-1].content


def test_palavra_chave_olha_a_pergunta_e_nao_o_nome_do_produto() -> None:
    llm = LLMScript(script=[AIMessage(content="")])
    with patch("venus_sdk.nodes.roteador.get_llm_rapido", return_value=llm):
        estado = no_roteador({"mensagem_usuario": PREFIXO + "e o retinol, faz mal?", "historico": []})
    assert estado["rota"] == "ingrediente"
    assert estado["pergunta_original"] == "e o retinol, faz mal?"
    assert estado["produto_em_contexto"] == "CeraVe Creme Hidratante"


@pytest.mark.parametrize("eco_do_llm", ["[monta minha rotina da noite]", "qual vc recomendaria?"])
def test_pergunta_original_e_a_mensagem_real_e_nao_o_eco_do_llm(eco_do_llm: str) -> None:
    llm = LLMScript(script=[AIMessage(content=f"ROUTE=rotina\nPERGUNTA_ORIGINAL={eco_do_llm}")])
    with patch("venus_sdk.nodes.roteador.get_llm_rapido", return_value=llm):
        estado = no_roteador({"mensagem_usuario": "monta minha rotina da noite", "historico": []})
    assert estado["pergunta_original"] == "monta minha rotina da noite"
    assert estado["produto_em_contexto"] is None


def _roteador_com_historico(resposta_llm: str, mensagem: str, anterior: str) -> dict:
    llm = LLMScript(script=[AIMessage(content=resposta_llm)])
    historico = [HumanMessage(content="oi"), AIMessage(content=anterior), HumanMessage(content=mensagem)]
    with patch("venus_sdk.nodes.roteador.get_llm_rapido", return_value=llm):
        return no_roteador({"mensagem_usuario": mensagem, "historico": historico})


def test_recapitulacao_da_conversa_pode_repetir_fatos_ja_ditos() -> None:
    estado = _roteador_com_historico(
        "A gente falou do CeraVe Creme Hidratante, que tem nota 63/100.",
        PREFIXO + "oq a gente tava falando antes mesmo?",
        anterior="O CeraVe Creme Hidratante recebeu nota 63/100.",
    )
    assert "63/100" in estado["resposta_final"]


def test_recapitulacao_com_numero_que_nao_esta_no_historico_e_substituida() -> None:
    estado = _roteador_com_historico(
        "A gente falou do CeraVe, que tem nota 90/100.",
        "oq a gente tava falando antes?",
        anterior="O CeraVe Creme Hidratante recebeu nota 63/100.",
    )
    assert "90" not in estado["resposta_final"]


# --- encerramento ---


@pytest.mark.parametrize("msg", ["não precisa mais nada", "não, obrigado", "só isso", "nada não", "Não."])
def test_encerramentos_sao_small_talk(msg: str) -> None:
    assert e_small_talk(msg)


@pytest.mark.parametrize("msg", ["não gostei do creme", "não tenho protetor, me indica um"])
def test_negativa_com_pedido_nao_e_small_talk(msg: str) -> None:
    assert not e_small_talk(msg)


def test_encerramento_roteado_para_especialista_vira_resposta_direta() -> None:
    llm = LLMScript(script=[AIMessage(content="ROUTE=rotina\nPERGUNTA_ORIGINAL=não precisa mais nada")])
    with patch("venus_sdk.nodes.roteador.get_llm_rapido", return_value=llm):
        estado = no_roteador({"mensagem_usuario": "não precisa mais nada", "historico": []})
    assert decidir_especialista(estado) == "direto"
    assert "fora do meu assunto" not in estado["resposta_final"]


# --- alergia a "Álcool" ---


def test_alergia_a_alcool_nao_casa_com_alcool_graxo() -> None:
    termos = termos_da_alergia("Álcool")
    assert termos == [TERMO_ALCOOL_ETILICO]
    for graxo in ("CETEARYL ALCOHOL", "CETYL ALCOHOL", "ÁLCOOL CETEARÍLICO", "PHENOXYETHANOL", "FENOXIETANOL"):
        assert not termo_bate(TERMO_ALCOOL_ETILICO, graxo), graxo
    for etilico in ("ALCOHOL", "ALCOHOL DENAT.", "Álcool", "ETHANOL", "SD ALCOHOL 40-B", "ÁLCOOL DESNATURADO"):
        assert termo_bate(TERMO_ALCOOL_ETILICO, etilico), etilico


def test_outras_alergias_continuam_por_substring() -> None:
    assert any(termo_bate(t, "parfum") for t in termos_da_alergia("Parfum"))
    assert any(termo_bate(t, "glycine soja oil") for t in termos_da_alergia("Soja"))


def test_rotina_nao_exclui_creme_com_alcool_graxo() -> None:
    favoritos = [
        {"product_id": 10, "name": "CeraVe Creme Reparador", "category_name": "Hidratante",
         "inci_names": ["cetearyl alcohol", "álcool cetearílico", "glycerin"]},
        {"product_id": 11, "name": "Tônico X", "category_name": "Tônico", "inci_names": ["alcohol denat."]},
    ]
    rotina = _montar_rotina(favoritos, _termos_da_alergia("Álcool"), "noite")
    assert [p["product_id"] for p in rotina["passos"]] == [10]
    assert [p["product_id"] for p in rotina["excluidos_por_alergia"]] == [11]


# --- histórico e produto em contexto para especialista e Juiz ---


def _estado_com_historico() -> dict:
    return {
        "rota": "produto",
        "pergunta_original": "sim, sugira",
        "produto_em_contexto": "CeraVe Creme Hidratante",
        "historico": [
            HumanMessage(content="cadastrei os produtos"),
            AIMessage(content="Quer que eu sugira alternativas sem álcool?"),
            HumanMessage(content=PREFIXO + "sim, sugira"),  # mensagem deste turno
        ],
    }


def test_historico_recente_exclui_a_mensagem_do_turno() -> None:
    historico = historico_recente(_estado_com_historico())
    assert historico == "Usuário: cadastrei os produtos\nVenus: Quer que eu sugira alternativas sem álcool?"
    assert historico_recente({"historico": [HumanMessage(content="oi")]}) is None


def test_especialista_recebe_historico_e_produto_em_contexto() -> None:
    entrada = _montar_entrada(_estado_com_historico())
    assert "PRODUTO_EM_CONTEXTO=CeraVe Creme Hidratante" in entrada
    assert "Venus: Quer que eu sugira alternativas sem álcool?" in entrada


def test_juiz_recebe_historico() -> None:
    estado = {**_estado_com_historico(), "resposta_especialista": {"dominio": "produto", "resposta": "x"}}
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content="RESULTADO=aprovado")
    with patch("venus_sdk.nodes.juiz.get_llm_juiz", return_value=llm):
        no_agente_juiz(estado)
    entrada = llm.invoke.call_args.args[0][1][1]
    assert "HISTORICO_RECENTE" in entrada and "alternativas sem álcool" in entrada


def test_juiz_aprova_pedido_de_esclarecimento_sem_chamar_o_llm() -> None:
    estado = {"rota": "produto", "pergunta_original": "qual vc recomendaria?", "resposta_especialista": {
        "dominio": "produto", "intencao": "sugerir", "resposta": "", "recomendacao": "", "fontes_usadas": [],
        "esclarecer": "Qual tipo de pele e necessidade você tem?"}}
    with patch("venus_sdk.nodes.juiz.get_llm_juiz", side_effect=AssertionError("não devia chamar o LLM")):
        veredito = no_agente_juiz(estado)
    assert veredito["aprovado_juiz"] is True


def test_esclarecimento_que_afirma_nota_continua_indo_para_o_llm() -> None:
    estado = {"rota": "produto", "pergunta_original": "x", "resposta_especialista": {
        "dominio": "produto", "resposta": "Ele tem nota 63/100.", "esclarecer": "Qual produto?"}}
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content="RESULTADO=reprovado\nFEEDBACK=sem fonte")
    with patch("venus_sdk.nodes.juiz.get_llm_juiz", return_value=llm):
        assert no_agente_juiz(estado)["aprovado_juiz"] is False


# --- resposta de fallback (Juiz esgotado) ---


def _ev(tool: str, resultado: object, **argumentos: object) -> dict:
    return {"tool": tool, "resultado": json.dumps(resultado), "argumentos": argumentos}


def test_fallback_explica_rotina_vazia_por_alergia() -> None:
    evidencias = [
        _ev("get_user_favorites", [{"name": "CeraVe Creme Reparador"}], user_id=142),
        _ev("suggest_routine", {"horario": "noite", "passos": [],
                                "excluidos_por_alergia": [{"product_id": 10, "nome": "CeraVe Creme Reparador",
                                                           "ingredientes_em_conflito": ["parfum"]}],
                                "sem_produto_para": ["Limpeza", "Hidratante"]}, user_id=142),
    ]
    texto = _resposta_segura_sem_aprovacao({"rota": "rotina", "evidencias_tools": evidencias})
    assert "Deixei de fora CeraVe Creme Reparador" in texto
    assert "Falta um produto salvo para: Limpeza, Hidratante" in texto
    assert "Seus produtos favoritos" not in texto


def _evidencias_produto(ingredientes: list[dict]) -> list[dict]:
    return [
        _ev("get_product", {"name": "CeraVe Creme Hidratante", "brand_name": "CeraVe",
                            "category_name": "Hidratante"}, product_id=17),
        _ev("get_product_ingredients", ingredientes, product_id=17),
        _ev("get_user_allergies", [{"allergy_name": "Parfum"}, {"allergy_name": "Álcool"}], user_id=142),
    ]


def test_fallback_cruza_ingredientes_com_alergias_sem_falso_positivo_de_alcool_graxo() -> None:
    evidencias = _evidencias_produto([
        {"common_name": "ÁLCOOL CETEARÍLICO", "inci_name": "CETEARYL ALCOHOL"},
        {"common_name": "GLICEROL", "inci_name": "GLYCERIN"},
    ])
    texto = _resposta_segura_sem_aprovacao({"rota": "produto", "pergunta_original": "x",
                                            "evidencias_tools": evidencias})
    assert "Nenhum ingrediente cadastrado dele bate com as alergias que você declarou" in texto


def test_fallback_aponta_ingrediente_que_bate_com_alergia() -> None:
    evidencias = _evidencias_produto([{"common_name": "", "inci_name": "PARFUM"}])
    texto = _resposta_segura_sem_aprovacao({"rota": "produto", "pergunta_original": "x",
                                            "evidencias_tools": evidencias})
    assert "Atenção" in texto and "PARFUM (alergia a Parfum)" in texto


def test_fallback_de_sugestao_lista_candidatos_sem_o_produto_da_tela() -> None:
    evidencias = [
        _ev("search_product", [{"product_id": 17, "name": "CeraVe Creme Hidratante", "brand_name": "CeraVe",
                                "tem_score": True}], termo="hidratante"),
        _ev("search_product", [{"product_id": 15, "name": "CeraVe Loção Facial", "brand_name": "CeraVe",
                                "tem_score": True}], termo="loção"),
        _ev("get_product", {"name": "CeraVe Creme Hidratante", "brand_name": "CeraVe"}, product_id=17),
        _ev("get_product_score", {"overall_score": 62}, product_id=15),
    ]
    texto = _resposta_segura_sem_aprovacao({
        "rota": "produto", "pergunta_original": "sim, sugira", "produto_em_contexto": "CeraVe Creme Hidratante",
        "resposta_especialista": {"dominio": "produto", "intencao": "sugerir"}, "evidencias_tools": evidencias,
    })
    assert "- CeraVe Loção Facial (CeraVe) — nota geral 62/100" in texto
    assert "Creme Hidratante" not in texto


# --- memória e busca ---


def test_memoria_aceita_json_cercado_por_markdown() -> None:
    assert _extrair_fatos_novos('```json\n{"tipo_pele": "oleosa"}\n```') == {"tipo_pele": "oleosa"}


def test_busca_remove_produto_duplicado_mesmo_nome_e_marca() -> None:
    resultado = [
        {"product_id": 17, "name": "CeraVe Creme Hidratante", "brand_name": "CeraVe"},
        {"product_id": 59, "name": "CeraVe Creme Hidratante", "brand_name": "CeraVe"},
        {"product_id": 3, "name": "CeraVe Creme-espuma de Limpeza", "brand_name": "CeraVe"},
    ]
    assert [p["product_id"] for p in _sem_duplicatas(resultado)] == [17, 3]
    assert _sem_duplicatas({"encontrado": False}) == {"encontrado": False}
