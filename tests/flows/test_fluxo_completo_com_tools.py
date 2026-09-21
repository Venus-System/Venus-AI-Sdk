"""Grafo COMPLETO (guardrail -> memória -> roteador -> especialista com tools
reais -> juiz -> orquestrador -> guardrail) com LLMs roteirizados. As tools
rodam de verdade (RAG local e pool falso); só o LLM é dublê."""

from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from _fakes import ConexaoFalsa, LLMScript, PoolFalso, chamada_tool
from langchain_core.messages import AIMessage

from venus_sdk.flows.venus_flow import compilar_grafo_venus
from venus_sdk.memory import criar_checkpointer_em_memoria
from venus_sdk.rag import criar_indice_local

FAQ = Path(__file__).resolve().parents[2] / "data" / "faq"
CFG = {"configurable": {"thread_id": "t1"}}


def _ai(texto: str) -> AIMessage:
    return AIMessage(content=texto)


def _patches(stack: ExitStack, *, router, juiz, especialista, orquestrador) -> None:
    stack.enter_context(patch("venus_sdk.nodes.roteador.get_llm_rapido", return_value=router))
    stack.enter_context(patch("venus_sdk.nodes.juiz.get_llm_rapido", return_value=juiz))
    stack.enter_context(patch("venus_sdk.nodes.especialistas.get_llm_especialista", return_value=especialista))
    stack.enter_context(patch("venus_sdk.nodes.orquestrador.get_llm_especialista", return_value=orquestrador))


def _faq_json(fontes: list[str], resposta: str = "O score vai de 0 a 100.") -> str:
    return json.dumps({"dominio": "faq", "intencao": "consultar_faq", "resposta": resposta,
                       "recomendacao": "", "fontes_usadas": fontes})


async def test_faq_rag_passa_pelo_juiz_e_cita_fonte() -> None:
    especialista = LLMScript(script=[
        chamada_tool("faq_retriever", {"pergunta": "como funciona o score?"}),
        _ai(_faq_json(["como_funciona_o_score.md"])),
    ])
    juiz = LLMScript(script=[_ai("RESULTADO=aprovado")])
    orq = LLMScript(script=[_ai("O score do Venus vai de 0 a 100.")])
    router = LLMScript(script=[_ai("ROUTE=faq\nPERGUNTA_ORIGINAL=como funciona o score?")])
    with ExitStack() as st:
        _patches(st, router=router, juiz=juiz, especialista=especialista, orquestrador=orq)
        grafo = compilar_grafo_venus(checkpointer=criar_checkpointer_em_memoria(), indice_rag=criar_indice_local(FAQ))
        estado = await grafo.ainvoke({"mensagem_usuario": "como funciona o score?"}, config=CFG)

    assert estado["resposta_final"] == "O score do Venus vai de 0 a 100."
    assert estado["aprovado_juiz"] is True
    assert estado["resposta_especialista"]["fontes_usadas"] == ["como_funciona_o_score.md"]
    # o juiz recebeu os trechos REAIS recuperados pela tool
    entrada_juiz = str(juiz.chamadas[0][-1].content)
    assert "RESULTADOS_TOOLS" in entrada_juiz and "como_funciona_o_score.md" in entrada_juiz


async def test_juiz_reprova_faq_e_volta_para_o_faq() -> None:
    especialista = LLMScript(script=[
        chamada_tool("faq_retriever", {"pergunta": "x"}),
        _ai(_faq_json([], "Invenção sem fonte.")),
        chamada_tool("faq_retriever", {"pergunta": "x"}, "c2"),
        _ai(_faq_json(["como_funciona_o_score.md"])),
    ])
    juiz = LLMScript(script=[_ai("RESULTADO=reprovado\nFEEDBACK=cite a fonte"), _ai("RESULTADO=aprovado")])
    orq = LLMScript(script=[_ai("Resposta final.")])
    router = LLMScript(script=[_ai("ROUTE=faq\nPERGUNTA_ORIGINAL=como funciona o score?")])
    with ExitStack() as st:
        _patches(st, router=router, juiz=juiz, especialista=especialista, orquestrador=orq)
        grafo = compilar_grafo_venus(indice_rag=criar_indice_local(FAQ))
        estado = await grafo.ainvoke({"mensagem_usuario": "como funciona o score?"})

    assert estado["tentativas_juiz"] == 2 and estado["aprovado_juiz"] is True
    assert len(router.chamadas) == 1  # retry volta pro especialista, não pro roteador
    assert "cite a fonte" in str(especialista.chamadas[2][1].content)  # feedback chegou ao especialista


async def test_rotina_usa_tools_do_pool() -> None:
    favoritos = [{"product_id": 2, "name": "Sérum", "category_name": "Sérum", "inci_names": ["niacinamide"]}]
    conexao = ConexaoFalsa(fetch=lambda q, *a: [] if "allergy_name" in q else favoritos)
    especialista = LLMScript(script=[
        chamada_tool("suggest_routine", {"user_id": 7, "horario": "noite"}),
        _ai(json.dumps({"dominio": "rotina", "intencao": "criar", "resposta": "Rotina montada.",
                        "recomendacao": "", "fontes_usadas": ["suggest_routine"]})),
    ])
    router = LLMScript(script=[_ai("ROUTE=rotina\nPERGUNTA_ORIGINAL=monta uma rotina de noite")])
    with ExitStack() as st:
        _patches(st, router=router, juiz=LLMScript(script=[_ai("RESULTADO=aprovado")]),
                 especialista=especialista, orquestrador=LLMScript(script=[_ai("Aqui está sua rotina.")]))
        grafo = compilar_grafo_venus(pool=PoolFalso(conexao))
        estado = await grafo.ainvoke({"mensagem_usuario": "monta uma rotina de noite", "usuario_id_postgres": 7})

    # o LLM falso não citou o produto: o orquestrador usa os passos reais da tool
    assert "Sérum" in estado["resposta_final"] and "Passos (noite)" in estado["resposta_final"]
    assert estado["evidencias_tools"][0]["tool"] == "suggest_routine"
    assert "USER_ID_POSTGRES=7" in str(especialista.chamadas[0][1].content)


async def test_falha_de_tool_nao_derruba_e_juiz_ve_o_erro() -> None:
    conexao = ConexaoFalsa(erro=ConnectionError("db fora"))
    especialista = LLMScript(script=[
        chamada_tool("get_user_profile", {"user_id": 7}),
        _ai(json.dumps({"dominio": "rotina", "intencao": "consultar", "resposta": "Não consegui acessar seu perfil.",
                        "recomendacao": "", "fontes_usadas": ["get_user_profile"]})),
    ])
    router = LLMScript(script=[_ai("ROUTE=rotina\nPERGUNTA_ORIGINAL=qual meu perfil?")])
    with ExitStack() as st:
        _patches(st, router=router, juiz=LLMScript(script=[_ai("RESULTADO=aprovado")]),
                 especialista=especialista, orquestrador=LLMScript(script=[_ai("Tive um problema.")]))
        grafo = compilar_grafo_venus(pool=PoolFalso(conexao))
        estado = await grafo.ainvoke({"mensagem_usuario": "qual meu perfil?", "usuario_id_postgres": 7})

    assert "erro" in estado["evidencias_tools"][0]["resultado"]
    assert estado["resposta_final"] == "Tive um problema."
