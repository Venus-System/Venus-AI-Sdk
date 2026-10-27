"""Juiz na rota produto (conversa real de 2026-10-20, em que o Juiz esgotava as
tentativas na maioria dos turnos): amarração nota <-> product_id conferida em
código, confirmação curta de afirmação do usuário, retry que mantém os
produtos e teto de chamadas de tool aplicado no código."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from _fakes import LLMScript, chamada_tool
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from venus_sdk.flows.agente_mcp import CHAMADA_RECUSADA, montar_agente_mcp
from venus_sdk.nodes._amarracao_produtos import conferir_amarracao, notas_citadas
from venus_sdk.nodes.especialistas import _extrair_evidencias_tools, _montar_entrada
from venus_sdk.nodes.juiz import no_agente_juiz

_BUSCA = [
    {"product_id": 17, "name": "CeraVe Creme Hidratante", "brand_name": "CeraVe"},
    {"product_id": 1, "name": "CeraVe Loção Facial Hidratante Noite", "brand_name": "CeraVe"},
]
EVIDENCIAS = [
    {"tool": "search_product", "argumentos": {"termo": "hidratante"}, "resultado": json.dumps(_BUSCA)},
    {"tool": "get_product_score", "argumentos": {"product_id": 1},
     "resultado": json.dumps({"overall_score": 76, "performance_score": None})},
    {"tool": "get_personalized_score", "argumentos": {"product_id": 17, "user_id": 142},
     "resultado": json.dumps({"final_score": 63, "recommendation_level": "acceptable"})},
]


def _especialista(resposta: str, produtos: list | None, **extra) -> dict:
    dados = {"dominio": "produto", "intencao": "sugerir", "resposta": resposta, "recomendacao": "",
             "fontes_usadas": ["search_product"], **extra}
    if produtos is not None:
        dados["produtos"] = produtos
    return dados


def _julgar(especialista: dict, evidencias: list | None = EVIDENCIAS, llm: str = "RESULTADO=aprovado"):
    with patch("venus_sdk.nodes.juiz.get_llm_juiz") as get_llm:
        get_llm.return_value.invoke.return_value.content = llm
        resultado = no_agente_juiz({"rota": "produto", "pergunta_original": "qual vc recomendaria?",
                                    "resposta_especialista": especialista, "evidencias_tools": evidencias,
                                    "tentativas_juiz": 0})
    return resultado, get_llm.return_value.invoke


# --- conferência determinística ---


def test_notas_citadas() -> None:
    assert notas_citadas("tem nota 63/100 e o outro nota geral de 76; score 50") == [63, 76, 50]
    assert notas_citadas("niacinamida a 4% e 2 passos") == []


def test_nota_de_outro_produto_com_o_nome_errado_reprova() -> None:
    """A nota 76 é do product_id 1 (Loção Facial Noite), citada como se fosse do Creme Hidratante."""
    problemas, _ = conferir_amarracao(_especialista(
        "O CeraVe Creme Hidratante tem nota 76/100.",
        [{"product_id": 1, "nome": "CeraVe Creme Hidratante", "notas": {"overall_score": 76}}],
    ), EVIDENCIAS)
    assert any("product_id 1 é \"CeraVe Loção Facial Hidratante Noite\"" in p for p in problemas)


def test_nota_que_nao_bate_com_a_tool_reprova() -> None:
    problemas, _ = conferir_amarracao(_especialista(
        "O CeraVe Creme Hidratante tem nota 70/100 pra você.",
        [{"product_id": 17, "nome": "CeraVe Creme Hidratante", "notas": {"final_score": 70}}],
    ), EVIDENCIAS)
    assert any("final_score do product_id 17 é 63" in p for p in problemas)


def test_nota_de_um_produto_citada_na_frase_do_outro_reprova() -> None:
    problemas, _ = conferir_amarracao(_especialista(
        "O CeraVe Creme Hidratante tem nota 76/100. A CeraVe Loção Facial Hidratante Noite fica com 63/100.",
        [{"product_id": 17, "nome": "CeraVe Creme Hidratante", "notas": {"final_score": 63}},
         {"product_id": 1, "nome": "CeraVe Loção Facial Hidratante Noite", "notas": {"overall_score": 76}}],
    ), EVIDENCIAS)
    assert len(problemas) == 2
    assert all("é de outro produto" in p for p in problemas)


def test_product_id_inventado_e_nota_sem_tool_reprovam() -> None:
    problemas, _ = conferir_amarracao(_especialista(
        "Sugiro o produto 99 e o CeraVe Creme Hidratante (nota 85/100).",
        [{"product_id": 99, "nome": "Inventado", "notas": {}},
         {"product_id": 17, "nome": "CeraVe Creme Hidratante", "notas": {"overall_score": 85}}],
    ), EVIDENCIAS)
    assert any("product_id 99 não veio de nenhuma tool" in p for p in problemas)
    assert any("overall_score=85 do product_id 17 não veio" in p for p in problemas)


def test_id_so_com_nota_consultada_e_campo_textual_da_tool_sao_aceitos() -> None:
    """Falsos positivos da reprodução real: o id 6 só aparecia no score (a busca
    dele foi na tentativa anterior) e `recommendation_level` não é numérico."""
    # A busca do id 6 veio da tentativa anterior do mesmo turno (evidência somada no retry).
    evidencias = [{"tool": "get_product_score", "argumentos": {"product_id": 6},
                   "resultado": json.dumps({"overall_score": 63})}, *EVIDENCIAS,
                  {"tool": "search_product", "argumentos": {"termo": "sérum"},
                   "resultado": json.dumps([{"product_id": 6, "name": "CeraVe Sérum Hidratante com Ácido Hialurônico"}])}]
    problemas, resumo = conferir_amarracao(_especialista(
        "O Sérum tem nota geral 63/100. O CeraVe Creme Hidratante é aceitável pra você.",
        [{"product_id": 6, "nome": "CeraVe Sérum Hidratante", "notas": {"overall_score": 63}},
         {"product_id": 17, "nome": "CeraVe Creme Hidratante", "notas": {"recommendation_level": "acceptable"}}],
    ), evidencias)
    assert problemas == []
    assert "product_id 6" in resumo


def test_score_nao_encontrado_nao_valida_id() -> None:
    evidencias = [{"tool": "get_personalized_score", "argumentos": {"product_id": 18, "user_id": 142},
                   "resultado": json.dumps({"encontrado": False, "mensagem": "não encontrado"})}]
    problemas, _ = conferir_amarracao(_especialista("Sugiro a Loção Noite.",
                                                    [{"product_id": 18, "nome": "Loção Noite", "notas": {}}]),
                                      evidencias)
    assert any("ache o id dele com search_product" in p for p in problemas)


def test_nota_no_texto_sem_campo_produtos_reprova() -> None:
    problemas, resumo = conferir_amarracao(_especialista("Ele tem nota 63/100.", None), EVIDENCIAS)
    assert problemas and resumo is None


def test_sem_produtos_e_sem_nota_nao_ha_o_que_conferir() -> None:
    assert conferir_amarracao(_especialista("Encontrei estes hidratantes.", None), EVIDENCIAS) == ([], None)


# --- veredito do Juiz ---


def test_amarracao_errada_reprova_sem_llm_com_feedback_exato() -> None:
    resultado, invoke = _julgar(_especialista(
        "O CeraVe Creme Hidratante tem nota 76/100.",
        [{"product_id": 1, "nome": "CeraVe Creme Hidratante", "notas": {"overall_score": 76}}],
    ))
    assert resultado["aprovado_juiz"] is False
    assert "product_id 1" in resultado["feedback_juiz"] and "mantendo os mesmos produtos" in resultado["feedback_juiz"]
    invoke.assert_not_called()


def test_so_nomes_e_notas_conferidos_aprova_sem_llm() -> None:
    resultado, invoke = _julgar(_especialista(
        "Pra você, o CeraVe Creme Hidratante tem nota 63/100. A CeraVe Loção Facial Hidratante Noite tem nota geral 76/100.",
        [{"product_id": 17, "nome": "CeraVe Creme Hidratante", "notas": {"final_score": 63}},
         {"product_id": 1, "nome": "CeraVe Loção Facial Hidratante Noite", "notas": {"overall_score": 76}}],
    ))
    assert resultado["aprovado_juiz"] is True
    invoke.assert_not_called()


@pytest.mark.parametrize(
    "afirmacao", ["e contém ceramidas", "e é adequado para pele oleosa", "e nota alta em segurança"]
)
def test_amarracao_conferida_com_afirmacao_alem_da_nota_ainda_vai_ao_llm(afirmacao: str) -> None:
    resultado, invoke = _julgar(_especialista(
        f"O CeraVe Creme Hidratante tem nota 63/100 {afirmacao}.",
        [{"product_id": 17, "nome": "CeraVe Creme Hidratante", "notas": {"final_score": 63}}],
    ))
    assert resultado["aprovado_juiz"] is True
    entrada = invoke.call_args[0][0][1][1]
    assert "AMARRACAO_VERIFICADA=product_id 17 = CeraVe Creme Hidratante (notas: final_score=63)" in entrada


def test_confirmacao_curta_de_afirmacao_aprova_sem_llm() -> None:
    resultado, invoke = _julgar(
        {"dominio": "produto", "intencao": "confirmar", "resposta": "Boa! Agora eles estão salvos aí.",
         "recomendacao": "", "fontes_usadas": [], "acompanhamento": "Quer que eu monte a rotina com eles?"},
        evidencias=None,
    )
    assert resultado["aprovado_juiz"] is True
    invoke.assert_not_called()


# --- retry mantém os produtos ---


def test_retry_informa_os_produtos_da_tentativa_reprovada() -> None:
    entrada = _montar_entrada({
        "rota": "produto", "pergunta_original": "vc acabou de contraindicar esse produto",
        "feedback_juiz": "corrija a nota",
        "resposta_especialista": {"produtos": [{"product_id": 17, "nome": "CeraVe Creme Hidratante"}]},
    })
    assert "PRODUTOS_DA_TENTATIVA_ANTERIOR=17 (CeraVe Creme Hidratante)" in entrada


def test_primeira_tentativa_nao_herda_produtos() -> None:
    entrada = _montar_entrada({"rota": "produto", "pergunta_original": "x",
                               "resposta_especialista": {"produtos": [{"product_id": 17, "nome": "y"}]}})
    assert "PRODUTOS_DA_TENTATIVA_ANTERIOR" not in entrada


# --- teto de chamadas de tool no código ---


def test_teto_de_chamadas_recusa_o_excedente_inclusive_em_lote() -> None:
    executadas: list[int] = []

    @tool
    async def get_product_score(product_id: int) -> dict:
        """Nota do produto."""
        executadas.append(product_id)
        return {"overall_score": product_id}

    lote = AIMessage(content="", tool_calls=[
        {"name": "get_product_score", "args": {"product_id": i}, "id": f"c{i}"} for i in (1, 2, 3)
    ])
    llm = LLMScript(script=[lote, chamada_tool("get_product_score", {"product_id": 4}, "c4"),
                            AIMessage(content='{"dominio": "produto"}')])
    agente = montar_agente_mcp(llm, tools=[get_product_score], max_chamadas_tools=2)
    mensagens = asyncio.run(agente.ainvoke({"messages": [("human", "x")]}))["messages"]

    assert sorted(executadas) == [1, 2]
    recusadas = [m for m in mensagens if str(getattr(m, "content", "")).startswith(CHAMADA_RECUSADA)]
    assert len(recusadas) == 2
    assert [e["argumentos"]["product_id"] for e in _extrair_evidencias_tools(mensagens)] == [1, 2]


# --- correções da rodada no chatbot real (2026-10-21) ---


def test_json_invalido_reprova_sem_llm() -> None:
    resultado, invoke = _julgar({"dominio": "rotina", "intencao": "erro_formato", "resposta": "x",
                                 "recomendacao": "", "fontes_usadas": []})
    assert resultado["aprovado_juiz"] is False
    assert "JSON válido" in resultado["feedback_juiz"]
    invoke.assert_not_called()


def _sugestao_com_contexto(produtos: list) -> dict:
    return {"rota": "produto", "pergunta_original": "sim, sugira", "produto_em_contexto": "CeraVe Creme Hidratante",
            "resposta_especialista": _especialista("Sugiro o CeraVe Creme Hidratante.", produtos),
            "evidencias_tools": EVIDENCIAS, "tentativas_juiz": 0}


def test_sugestao_que_so_oferece_o_produto_em_contexto_reprova_sem_llm() -> None:
    with patch("venus_sdk.nodes.juiz.get_llm_juiz") as get_llm:
        resultado = no_agente_juiz(_sugestao_com_contexto(
            [{"product_id": 17, "nome": "CeraVe Creme Hidratante", "notas": {}}]))
    assert resultado["aprovado_juiz"] is False
    assert "PRODUTO_EM_CONTEXTO" in resultado["feedback_juiz"]
    get_llm.return_value.invoke.assert_not_called()


def test_sugestao_de_alternativa_ao_produto_em_contexto_nao_e_barrada() -> None:
    with patch("venus_sdk.nodes.juiz.get_llm_juiz") as get_llm:
        get_llm.return_value.invoke.return_value.content = "RESULTADO=aprovado"
        estado = _sugestao_com_contexto(
            [{"product_id": 1, "nome": "CeraVe Loção Facial Hidratante Noite", "notas": {}}])
        estado["resposta_especialista"]["resposta"] = "Sugiro a CeraVe Loção Facial Hidratante Noite."
        resultado = no_agente_juiz(estado)
    assert resultado["aprovado_juiz"] is True


def test_entrada_do_juiz_sem_campos_inuteis_das_tools() -> None:
    evidencias = [EVIDENCIAS[0], {"tool": "get_product", "argumentos": {"product_id": 17},
                                  "resultado": json.dumps(json.dumps({"name": "CeraVe Creme Hidratante",
                                                                      "description": "texto longo", "slug": "x"}))}]
    _, invoke = _julgar(_especialista("O CeraVe Creme Hidratante contém ceramidas.",
                                      [{"product_id": 17, "nome": "CeraVe Creme Hidratante", "notas": {}}]),
                        evidencias=evidencias)
    entrada = invoke.call_args[0][0][1][1]
    assert "CeraVe Creme Hidratante" in entrada
    assert "texto longo" not in entrada and "slug" not in entrada


def test_agente_de_rotina_recebe_teto_de_chamadas() -> None:
    import venus_sdk.nodes.especialistas as especialistas

    recebidos: dict = {}

    def _montar(llm, *, prompt, tools, **limites):
        recebidos.update(limites)
        return "agente"

    with patch.object(especialistas, "montar_agente_mcp", side_effect=_montar), \
         patch.object(especialistas, "get_llm_especialista", return_value=None), \
         patch.object(especialistas, "_executar_especialista", return_value={}):
        asyncio.run(especialistas.montar_no_agente_rotina(object())({"pergunta_original": "monta minha rotina"}))
    assert recebidos == {"max_chamadas_tools": especialistas.MAX_CHAMADAS_TOOLS_ROTINA}


_ROTINA = {"horario": "noite", "passos": [
    {"ordem": 1, "product_id": 8, "nome": "CeraVe Espuma Cremosa de Limpeza Hidratante", "categoria": "Gel de Limpeza"},
    {"ordem": 2, "product_id": 10, "nome": "CeraVe Creme Reparador para as Mãos", "categoria": "Hidratante"},
], "excluidos_por_alergia": [], "sem_produto_para": []}


def _julgar_rotina(resposta: str, rotina: dict = _ROTINA, **extra):
    with patch("venus_sdk.nodes.juiz.get_llm_juiz") as get_llm:
        get_llm.return_value.invoke.return_value.content = "RESULTADO=reprovado\nFEEDBACK=x"
        resultado = no_agente_juiz({
            "rota": "rotina", "pergunta_original": "Monta minha rotina da noite (só para essa semana)",
            "resposta_especialista": {"dominio": "rotina", "intencao": "criar", "resposta": resposta,
                                      "recomendacao": "", "fontes_usadas": ["suggest_routine"], **extra},
            "evidencias_tools": [{"tool": "suggest_routine", "argumentos": {"horario": "noite", "user_id": 142},
                                  "resultado": json.dumps(rotina)}],
            "tentativas_juiz": 0})
    return resultado, get_llm.return_value.invoke


def test_rotina_que_repete_os_passos_da_tool_aprova_sem_llm() -> None:
    resultado, invoke = _julgar_rotina(
        "1) CeraVe Espuma Cremosa de Limpeza Hidratante; 2) CeraVe Creme Reparador para as Maos")
    assert resultado["aprovado_juiz"] is True
    invoke.assert_not_called()


def test_rotina_que_omite_passo_ou_esconde_exclusao_por_alergia_vai_ao_llm() -> None:
    _, invoke = _julgar_rotina("1) CeraVe Espuma Cremosa de Limpeza Hidratante")
    invoke.assert_called_once()
    com_exclusao = {**_ROTINA, "excluidos_por_alergia": [{"nome": "Creme X"}]}
    _, invoke = _julgar_rotina(
        "1) CeraVe Espuma Cremosa de Limpeza Hidratante; 2) CeraVe Creme Reparador para as Mãos", com_exclusao)
    invoke.assert_called_once()


def test_retry_soma_a_evidencia_da_tentativa_anterior_do_turno() -> None:
    from unittest.mock import AsyncMock

    import venus_sdk.nodes.especialistas as especialistas

    anterior = [{"tool": "search_product", "argumentos": {"termo": "hidratante"}, "resultado": "[]"},
                {"tool": "get_user_allergies", "argumentos": {"user_id": 142}, "resultado": "{}"}]
    nova = [{"tool": "get_user_allergies", "argumentos": {"user_id": 142}, "resultado": '{"novo": 1}'}]
    with patch.object(especialistas, "_resposta_agente", new_callable=AsyncMock,
                      return_value=('{"domínio": "produto", "intencao": "sugerir"}', nova)):
        retry = asyncio.run(especialistas._executar_especialista(
            {"rota": "produto", "tentativas_juiz": 1, "evidencias_tools": anterior}, "produto", object()))
        novo_turno = asyncio.run(especialistas._executar_especialista(
            {"rota": "produto", "tentativas_juiz": 0, "evidencias_tools": anterior}, "produto", object()))

    assert [e["tool"] for e in retry["evidencias_tools"]] == ["get_user_allergies", "search_product"]
    assert retry["evidencias_tools"][0]["resultado"] == '{"novo": 1}'
    assert novo_turno["evidencias_tools"] == nova
    assert retry["resposta_especialista"]["dominio"] == "produto"


def test_id_consultado_sem_busca_e_chute() -> None:
    """Chatbot real: o modelo de fallback varreu get_product(1..6) e apresentou os achados."""
    evidencias = [{"tool": "get_product", "argumentos": {"product_id": 3},
                   "resultado": json.dumps({"name": "CeraVe Creme-espuma de Limpeza Hidratante"})}]
    problemas, _ = conferir_amarracao(_especialista(
        "Sua rotina tem o CeraVe Creme-espuma de Limpeza Hidratante.",
        [{"product_id": 3, "nome": "CeraVe Creme-espuma de Limpeza Hidratante", "notas": {}}]), evidencias)
    assert any("id chutado" in p for p in problemas)


def test_confirmar_que_consultou_produtos_vai_ao_llm() -> None:
    _, invoke = _julgar({"dominio": "produto", "intencao": "confirmar", "resposta":
                         "Sua rotina tem o CeraVe Creme Hidratante.", "recomendacao": "", "fontes_usadas": [],
                         "produtos": [{"product_id": 17, "nome": "CeraVe Creme Hidratante", "notas": {}}]})
    invoke.assert_called_once()


def test_resposta_final_sem_ids_internos() -> None:
    from venus_sdk.nodes.orquestrador import _sem_ids_internos

    assert _sem_ids_internos("CeraVe Creme (product_id 3) e Loção (product_id: 1).") == "CeraVe Creme e Loção."
    assert _sem_ids_internos("nota 63/100") == "nota 63/100"


@pytest.mark.parametrize("mensagem, esperado", [
    ("cadastrei os produtos de limpeza e hidratante que vc falou", True),
    ("comprei o hidratante", True),
    ("cadastrei, qual vc recomenda agora?", False),
    ("comprei e não funcionou", False),
    ("salvei mas deu coceira", False),
    ("vc acabou de contraindicar esse produto", False),
    ("sim, sugira", False),
])
def test_e_so_afirmacao(mensagem: str, esperado: bool) -> None:
    from venus_sdk.nodes.especialistas import e_so_afirmacao

    assert e_so_afirmacao(mensagem) is esperado


def test_afirmacao_vira_confirmacao_sem_llm_e_o_juiz_aprova() -> None:
    import venus_sdk.nodes.especialistas as especialistas

    with patch.object(especialistas, "montar_agente_mcp", side_effect=AssertionError("sem agente")), \
         patch.object(especialistas, "get_llm_especialista", side_effect=AssertionError("sem LLM")):
        estado = asyncio.run(especialistas.montar_no_agente_produto(object())(
            {"rota": "produto", "pergunta_original": "cadastrei os produtos que vc falou"}))
    assert estado["resposta_especialista"]["intencao"] == "confirmar"
    assert estado["evidencias_tools"] is None
    resultado, invoke = _julgar(estado["resposta_especialista"], evidencias=None)
    assert resultado["aprovado_juiz"] is True
    invoke.assert_not_called()


def test_rotina_com_enfeite_de_uso_aprova_sem_llm_mas_alergia_vai_ao_llm() -> None:
    passos = "1) CeraVe Espuma Cremosa de Limpeza Hidratante, que limpa sem ressecar; 2) CeraVe Creme Reparador para as Mãos"
    resultado, invoke = _julgar_rotina(passos)
    assert resultado["aprovado_juiz"] is True
    invoke.assert_not_called()
    _, invoke = _julgar_rotina(passos + ". Nenhum tem álcool.")
    invoke.assert_called_once()


def test_reserva_do_juiz_esgotado_traz_nota_personalizada_do_produto_em_tela() -> None:
    from venus_sdk.nodes.orquestrador import _resposta_segura_sem_aprovacao

    evidencias = [
        *EVIDENCIAS[:2],
        {"tool": "get_personalized_score", "argumentos": {"product_id": 1, "user_id": 142},
         "resultado": json.dumps({"final_score": 20, "summary": "Nota 20 de 100 - Contraindicado."})},
        {"tool": "get_personalized_score", "argumentos": {"product_id": 17, "user_id": 142},
         "resultado": json.dumps({"final_score": 63, "summary": "Nota 63 de 100 - Aceitável."})},
    ]
    texto = _resposta_segura_sem_aprovacao({
        "rota": "produto", "pergunta_original": "vc acabou de contraindicar esse produto",
        "produto_em_contexto": "CeraVe Creme Hidratante", "evidencias_tools": evidencias,
        "resposta_especialista": {"intencao": "explicar_recomendacao"}})
    assert "CeraVe Creme Hidratante" in texto
    assert "Pra você: Nota 63 de 100 - Aceitável." in texto
    assert "Contraindicado" not in texto


def test_reserva_sem_summary_mostra_o_nivel_de_recomendacao() -> None:
    from venus_sdk.nodes.orquestrador import _resposta_segura_sem_aprovacao

    texto = _resposta_segura_sem_aprovacao({
        "rota": "produto", "pergunta_original": "vc acabou de contraindicar esse produto",
        "produto_em_contexto": "CeraVe Creme Hidratante", "evidencias_tools": EVIDENCIAS,
        "resposta_especialista": {"intencao": "explicar_recomendacao"}})
    assert "Nota pra você: 63/100 (aceitável)." in texto


def test_reserva_de_pedido_de_recomendacao_lista_opcoes_em_vez_da_ficha_de_um_produto() -> None:
    from venus_sdk.nodes.orquestrador import _resposta_segura_sem_aprovacao

    texto = _resposta_segura_sem_aprovacao({
        "rota": "produto", "pergunta_original": "qual vc recomendaria?", "evidencias_tools": EVIDENCIAS,
        "resposta_especialista": {"intencao": "explicar_recomendacao"}})
    assert texto.startswith("Olha o que encontrei no nosso catálogo")
    assert "CeraVe Loção Facial Hidratante Noite (CeraVe) — nota geral 76/100" in texto


def _orquestrar_rotina(texto_do_llm: str, resposta_do_especialista: str) -> str:
    from types import SimpleNamespace

    from venus_sdk.nodes.orquestrador import no_orquestrador

    estado = {
        "rota": "rotina", "aprovado_juiz": True,
        "pergunta_original": "posso fazer algo as 22h da noite sobre rotina ou estou ocupado? e de tarde?",
        "resposta_especialista": {"dominio": "rotina", "intencao": "consultar", "resposta": resposta_do_especialista,
                                  "recomendacao": "", "fontes_usadas": ["suggest_routine"]},
        "evidencias_tools": [{"tool": "suggest_routine", "argumentos": {"horario": "noite", "user_id": 142},
                              "resultado": json.dumps(_ROTINA)}],
    }
    with patch("venus_sdk.nodes.orquestrador.get_llm_orquestrador") as get_llm:
        get_llm.return_value.invoke.return_value = SimpleNamespace(content=texto_do_llm)
        return no_orquestrador(estado)["resposta_final"]


def test_texto_que_omite_passo_da_rotina_cai_no_texto_aprovado_sem_perder_a_tarde() -> None:
    """Chatbot real: o LLM esqueceu 1 dos produtos e a resposta inteira virou a
    lista da tool, perdendo a parte "e de tarde?"."""
    final = _orquestrar_rotina(
        "Às 22h dá pra fazer sua rotina com o CeraVe Creme Reparador para as Mãos! De tarde não há rotina.",
        "Às 22h: 1) CeraVe Espuma Cremosa de Limpeza Hidratante; 2) CeraVe Creme Reparador para as Mãos. "
        "Não há rotina de tarde — o Venus monta rotinas só de manhã e de noite.")
    assert "CeraVe Espuma Cremosa de Limpeza Hidratante" in final
    assert "tarde" in final


def test_se_o_texto_aprovado_tambem_omite_passo_usa_a_lista_da_tool() -> None:
    final = _orquestrar_rotina("Às 22h use o CeraVe Creme Reparador para as Mãos.",
                               "Use o CeraVe Creme Reparador para as Mãos.")
    assert final.startswith("Montei sua rotina com o que você já tem salvo")


def test_reserva_da_rotina_responde_a_tarde_e_a_agenda() -> None:
    """Chatbot real: com o Juiz esgotado, "posso fazer algo às 22h ou estou
    ocupado? e de tarde?" recebia só a lista da rotina."""
    from venus_sdk.nodes.orquestrador import _resposta_segura_sem_aprovacao

    texto = _resposta_segura_sem_aprovacao({
        "rota": "rotina",
        "pergunta_original": "posso fazer algo as 22h da noite sobre rotina ou estou ocupado? e de tarde?",
        "evidencias_tools": [{"tool": "suggest_routine", "argumentos": {"horario": "noite", "user_id": 142},
                              "resultado": json.dumps(_ROTINA)}]})
    assert "CeraVe Espuma Cremosa de Limpeza Hidratante" in texto
    assert "Não tenho acesso à sua agenda" in texto
    assert "De tarde não tem rotina" in texto


def test_reserva_da_rotina_sem_pergunta_de_tarde_nao_inventa_aviso() -> None:
    from venus_sdk.nodes.orquestrador import _resposta_segura_sem_aprovacao

    texto = _resposta_segura_sem_aprovacao({
        "rota": "rotina", "pergunta_original": "monta minha rotina da noite",
        "evidencias_tools": [{"tool": "suggest_routine", "argumentos": {"horario": "noite", "user_id": 142},
                              "resultado": json.dumps(_ROTINA)}]})
    assert "tarde" not in texto and "agenda" not in texto


def test_rotina_citada_sem_a_marca_na_frente_aprova_sem_llm() -> None:
    resultado, invoke = _julgar_rotina(
        "Às 22h: 1) Espuma Cremosa de Limpeza Hidratante; 2) Creme Reparador para as Mãos. "
        "De tarde não há rotina.")
    assert resultado["aprovado_juiz"] is True
    invoke.assert_not_called()


def test_texto_aprovado_que_cita_os_passos_sem_a_marca_e_aproveitado() -> None:
    final = _orquestrar_rotina(
        "Às 22h dá pra fazer sua rotina!",
        "Às 22h: 1) Espuma Cremosa de Limpeza Hidratante; 2) Creme Reparador para as Mãos. De tarde não há rotina.")
    assert final.startswith("Às 22h: 1) Espuma Cremosa")
    assert "tarde" in final



@pytest.mark.parametrize("agenda, esperado", [
    ({"conectado": True, "ocupado": True, "compromissos": [{"start": "x"}]}, "você tem compromisso"),
    ({"conectado": True, "ocupado": False, "compromissos": []}, "você está livre"),
    ({"conectado": False}, "não está conectada"),
    ({"erro": "não consegui checar o Google Calendar agora"}, "Não consegui ver sua agenda agora"),
])
def test_reserva_usa_o_resultado_da_agenda_quando_ela_foi_consultada(agenda: dict, esperado: str) -> None:
    from venus_sdk.nodes.orquestrador import _resposta_segura_sem_aprovacao

    texto = _resposta_segura_sem_aprovacao({
        "rota": "rotina", "pergunta_original": "posso fazer minha rotina às 22h ou estou ocupado?",
        "evidencias_tools": [
            {"tool": "check_availability", "argumentos": {"user_id": 142}, "resultado": json.dumps(agenda)},
            {"tool": "suggest_routine", "argumentos": {"horario": "noite", "user_id": 142},
             "resultado": json.dumps(_ROTINA)}]})
    assert esperado in texto
    assert "Não tenho acesso à sua agenda" not in texto



def test_nota_de_copia_homonima_do_produto_e_aceita_mas_de_outro_produto_nao() -> None:
    """Chatbot real: a busca trouxe o id 59 e a nota personalizada era do 17 —
    as duas cópias são o "CeraVe Creme Hidratante"."""
    evidencias = [*EVIDENCIAS, {"tool": "search_product", "argumentos": {"termo": "creme"},
                                "resultado": json.dumps([{"product_id": 59, "name": "CeraVe Creme Hidratante"}])}]
    item = {"product_id": 59, "nome": "CeraVe Creme Hidratante", "notas": {"final_score": 63}}
    problemas, _ = conferir_amarracao(_especialista("O CeraVe Creme Hidratante tem nota 63/100 pra você.", [item]),
                                      evidencias)
    assert problemas == []
    item_errado = {"product_id": 59, "nome": "CeraVe Creme Hidratante", "notas": {"overall_score": 76}}
    problemas, _ = conferir_amarracao(_especialista("O CeraVe Creme Hidratante tem nota 76/100.", [item_errado]),
                                      evidencias)
    assert problemas


def test_rotina_que_sugere_produto_fora_dos_passos_vai_ao_llm() -> None:
    """Chatbot real (2026-10-26): o agente de rotina "sugeriu" produtos que nenhuma tool trouxe."""
    _, invoke = _julgar_rotina(
        "1) CeraVe Espuma Cremosa de Limpeza Hidratante; 2) CeraVe Creme Reparador para as Mãos. "
        "Por exemplo, adicione o La Roche-Posay Toleriane aos favoritos.")
    invoke.assert_called_once()
