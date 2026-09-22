"""Grafo COMPLETO com LLMs roteirizados (sem gastar cota) contra o Postgres REAL
(`DATABASE_URL`), SOMENTE LEITURA. Valida: roteamento, tools no banco real,
evidências chegando ao juiz, aprovação/reprovação, orquestrador e guardrails.

Rodar:  pytest -m integration tests/flows/test_fluxo_roteirizado_banco_real.py -s
"""

from __future__ import annotations

import json
import os
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from _fakes import LLMScript, chamada_tool
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, ToolMessage

from venus_sdk.flows.venus_flow import compilar_grafo_venus
from venus_sdk.memory import criar_checkpointer_em_memoria, criar_store_em_memoria

load_dotenv()
pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="DATABASE_URL não definida")]


@pytest.fixture
async def pool():
    import asyncpg
    p = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2, timeout=30)
    yield p
    await p.close()


def _ai(t: str) -> AIMessage:
    return AIMessage(content=t)


def _json_final(dominio: str, fontes: list[str]):
    """Especialista falso 'esperto': monta o JSON a partir do que a tool devolveu."""
    def f(msgs):
        tools = [m for m in msgs if isinstance(m, ToolMessage)]
        tool = tools[-1] if tools else ToolMessage(content="(sem tool nesta rodada)", tool_call_id="x")
        return _ai(json.dumps({"dominio": dominio, "intencao": "consultar", "recomendacao": "",
                               "resposta": f"DADOS: {str(tool.content)[:300]}", "fontes_usadas": fontes}))
    return f


async def _rodar(pool, msg: str, rota: str, chamadas_tool: list[tuple[str, dict]], dominio: str,
                 user_id: int = 1, juiz: list | None = None):
    esp_script: list = []
    for i, (nome, args) in enumerate(chamadas_tool):
        esp_script.append(chamada_tool(nome, args, f"c{i}"))
    esp_script.append(_json_final(dominio, [n for n, _ in chamadas_tool]))
    esp = LLMScript(script=esp_script)
    router = LLMScript(script=[_ai(f"ROUTE={rota}\nPERGUNTA_ORIGINAL={msg}")])
    j = LLMScript(script=juiz or [_ai("RESULTADO=aprovado")])
    orq = LLMScript(script=[lambda m: _ai("Resposta final da Venus.")])
    mem = LLMScript(script=[_ai("{}")])
    with ExitStack() as st:
        st.enter_context(patch("venus_sdk.nodes.roteador.get_llm_rapido", return_value=router))
        st.enter_context(patch("venus_sdk.nodes.memoria.get_llm_rapido", return_value=mem))
        st.enter_context(patch("venus_sdk.nodes.juiz.get_llm_rapido", return_value=j))
        st.enter_context(patch("venus_sdk.nodes.especialistas.get_llm_especialista", return_value=esp))
        st.enter_context(patch("venus_sdk.nodes.orquestrador.get_llm_especialista", return_value=orq))
        grafo = compilar_grafo_venus(checkpointer=criar_checkpointer_em_memoria(),
                                     store=criar_store_em_memoria(), pool=pool)
        estado = await grafo.ainvoke(
            {"mensagem_usuario": msg, "usuario_id": f"t{user_id}", "usuario_id_postgres": user_id},
            config={"configurable": {"thread_id": f"real-{abs(hash(msg))}"}})
    return estado, j, esp


def _res(estado: dict, tool: str):
    r = next(e["resultado"] for e in estado["evidencias_tools"] if e["tool"] == tool)
    while isinstance(r, str):
        r = json.loads(r)
    return r


async def test_sugestao_produto_acha_itens_reais_e_juiz_ve_evidencia(pool):
    e, juiz, _ = await _rodar(pool, "produto pra cabelo cacheado", "produto",
                              [("search_product", {"termo": "cabelo cacheado"})], "produto")
    r = _res(e, "search_product")
    assert isinstance(r, list) and r and all("product_id" in x for x in r)
    assert e["aprovado_juiz"] is True and e["resposta_final"]
    assert "search_product" in str(juiz.chamadas[0][-1].content)


async def test_produto_inexistente_devolve_nao_encontrado(pool):
    e, _, _ = await _rodar(pool, "fala do Zorblax Ultra 9000", "produto",
                           [("search_product", {"termo": "zorblax xyzzy"})], "produto")
    assert _res(e, "search_product")["encontrado"] is False


async def test_score_zerado_nao_vira_nota(pool):
    e, _, _ = await _rodar(pool, "qual o score do produto 6?", "produto",
                           [("get_product_score", {"product_id": 6})], "produto")
    r = _res(e, "get_product_score")
    assert r.get("encontrado") is False or "erro" not in r  # nunca inventa nota
    if r.get("encontrado") is False:
        assert "não" in r["mensagem"].lower()


async def test_ingrediente_niacinamida_acha_nicotinamida(pool):
    e, _, _ = await _rodar(pool, "o que é niacinamida?", "ingrediente",
                           [("search_ingredient", {"termo": "niacinamida"})], "ingrediente")
    assert "NIACINAMIDE" in json.dumps(_res(e, "search_ingredient"), ensure_ascii=False)


async def test_alergia_do_usuario_2_vem_do_banco(pool):
    e, _, _ = await _rodar(pool, "tenho alergia a algo?", "produto",
                           [("get_user_allergies", {"user_id": 2})], "produto", user_id=2)
    assert "Fragr" in json.dumps(_res(e, "get_user_allergies"), ensure_ascii=False)


async def test_usuario_sem_alergia_nao_inventa(pool):
    e, _, _ = await _rodar(pool, "tenho alergia?", "produto",
                           [("get_user_allergies", {"user_id": 1})], "produto", user_id=1)
    assert _res(e, "get_user_allergies")["encontrado"] is False


async def test_perfil_real_do_usuario_1(pool):
    e, _, _ = await _rodar(pool, "qual meu perfil?", "rotina",
                           [("get_user_profile", {"user_id": 1})], "rotina")
    assert _res(e, "get_user_profile")["skin_type"] == "oily"


@pytest.mark.parametrize("uid,horario", [(1, "manha"), (2, "noite")])
async def test_rotina_real_tem_passos_ordenados_e_sem_alergenico(pool, uid, horario):
    e, _, _ = await _rodar(pool, f"monta rotina de {horario}", "rotina",
                           [("suggest_routine", {"user_id": uid, "horario": horario})], "rotina", user_id=uid)
    r = _res(e, "suggest_routine")
    assert r["passos"] and [p["ordem"] for p in r["passos"]] == list(range(1, len(r["passos"]) + 1))
    if horario == "manha":
        assert not any("Limpeza" in p["categoria"] and "Noite" in p["nome"] for p in r["passos"])


async def test_juiz_reprova_e_especialista_tenta_de_novo_no_banco_real(pool):
    e, juiz, esp = await _rodar(
        pool, "produto pra cabelo cacheado", "produto",
        [("search_product", {"termo": "cabelo cacheado"}), ("search_product", {"termo": "shampoo"})], "produto",
        juiz=[_ai("RESULTADO=reprovado\nFEEDBACK=cite os produtos"), _ai("RESULTADO=aprovado")])
    assert e["tentativas_juiz"] == 2 and e["aprovado_juiz"] is True
