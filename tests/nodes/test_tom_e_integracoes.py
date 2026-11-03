"""Tom da Venus com conferência, MCP/A2A no FAQ e roteador sem afirmar fatos."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from venus_sdk.nodes.fluxo_fixo import evidencias_de_faq
from venus_sdk.nodes.orquestrador import no_orquestrador
from venus_sdk.nodes.roteador import _aplicar_redes_de_seguranca

EVIDENCIAS = [{"tool": "search_product", "argumentos": {}, "resultado": json.dumps(
    [{"product_id": 5, "name": "Sérum X", "brand_name": "Marca"}])},
    {"tool": "get_product_score", "argumentos": {"product_id": 5}, "resultado": json.dumps({"overall_score": 58})}]


def _responder(reescrita, veredito="APROVADO"):
    estado = {"rota": "produto", "aprovado_juiz": True, "pergunta_original": "qual a nota do Sérum X?",
              "evidencias_tools": EVIDENCIAS,
              "resposta_especialista": {"dominio": "produto", "intencao": "informar", "resposta": ""}}
    with patch.dict("os.environ", {"VENUS_TOM_LLM": "1", "VENUS_RESPOSTA_SO_DAS_TOOLS": "produto,faq"}), \
            patch("venus_sdk.nodes.orquestrador.get_llm_orquestrador") as tom, \
            patch("venus_sdk.nodes.juiz.get_llm_juiz") as juiz:
        tom.return_value.invoke.return_value = SimpleNamespace(content=reescrita)
        juiz.return_value.invoke.return_value = SimpleNamespace(content=veredito)
        return no_orquestrador(estado)["resposta_final"], juiz


def test_reescrita_fiel_e_aprovada_chega_ao_usuario():
    texto, _ = _responder("Oii! O Sérum X tem nota geral 58/100, viu?")
    assert texto.startswith("Oii!")


def test_numero_novo_cai_no_texto_original_sem_chamar_o_juiz():
    texto, juiz = _responder("Oii! O Sérum X tem nota 90/100.")
    assert "90" not in texto and "58/100" in texto
    juiz.return_value.invoke.assert_not_called()


def test_produto_omitido_cai_no_texto_original():
    assert _responder("A nota é 58/100!")[0].startswith("Sérum X")


def test_juiz_reprova_fato_acrescentado():
    texto, _ = _responder("O Sérum X tem nota 58/100 e tira rugas!", "REPROVADO")
    assert "rugas" not in texto and "58/100" in texto


class _Tool:
    def __init__(self, nome, args, saida, metadata=None):
        self.name, self.args, self.saida, self.metadata, self.chamadas = nome, args, saida, metadata, []

    async def ainvoke(self, argumentos):
        self.chamadas.append(argumentos)
        if isinstance(self.saida, Exception):
            raise self.saida
        return self.saida


def _tools_faq(**extras):
    return {"faq_retriever": _Tool("faq_retriever", {"pergunta": {}}, {"encontrado": False}),
            "buscar_na_web": _Tool("buscar_na_web", {"consulta": {}}, [{"trecho": "web"}]), **extras}


async def test_sem_documento_consulta_a2a_e_mcp_mas_nao_a_web():
    a2a = _Tool("consultar_agente_externo", {"agente": {}, "pergunta": {}}, {"resposta": "Do agente."}, {"agentes": ["x"]})
    mcp = _Tool("base", {"query": {}}, "Do MCP.")
    tools = _tools_faq(consultar_agente_externo=a2a, base=mcp)
    with patch("venus_sdk.nodes.fluxo_fixo.get_llm_rapido") as llm:
        llm.return_value.invoke.return_value = SimpleNamespace(content="1")
        evidencias, fontes = await evidencias_de_faq("p", tools)
    assert a2a.chamadas == [{"agente": "x", "pergunta": "p"}] and mcp.chamadas == [{"query": "p"}]
    assert tools["buscar_na_web"].chamadas == []
    assert fontes == [{"texto": "Do agente.", "fonte": "agente x (A2A)", "externa": True}] and len(evidencias) == 3


async def test_integracao_fora_do_ar_nao_derruba():
    tools = _tools_faq(base=_Tool("base", {"query": {}}, ConnectionError("caiu")))
    assert (await evidencias_de_faq("p", tools))[1] == []


def test_fonte_externa_vem_com_aviso_e_sem_reescrita():
    estado = {"rota": "faq", "aprovado_juiz": True, "pergunta_original": "p", "evidencias_tools": [],
              "resposta_especialista": {"dominio": "faq", "intencao": "informar", "resposta": "",
                                        "fontes_a_mostrar": [{"texto": "Do agente.", "fonte": "agente x (A2A)", "externa": True}]}}
    with patch.dict("os.environ", {"VENUS_TOM_LLM": "1", "VENUS_RESPOSTA_SO_DAS_TOOLS": "faq"}), \
            patch("venus_sdk.nodes.orquestrador.get_llm_orquestrador") as tom:
        texto = no_orquestrador(estado)["resposta_final"]
    tom.assert_not_called()
    assert texto.startswith("Encontrei isto em uma fonte externa") and "Fonte: agente x (A2A)" in texto


@pytest.mark.parametrize("mensagem", ["qual a diferença entre pele mista e oleosa?", "como funciona o app?", "vocês têm loja?"])
def test_pergunta_do_assunto_nao_fica_na_resposta_direta(mensagem):
    assert _aplicar_redes_de_seguranca(None, "Inventei.", mensagem)[0] in ("faq", "produto", "ingrediente", "rotina")


@pytest.mark.parametrize("mensagem", ["oi, tudo bem?", "qual a capital da França?", "conta uma piada"])
def test_conversa_continua_na_resposta_direta(mensagem):
    assert _aplicar_redes_de_seguranca(None, "Oi!", mensagem)[0] is None


def test_faq_nao_passa_pela_reescrita():
    estado = {"rota": "faq", "aprovado_juiz": True, "pergunta_original": "p", "evidencias_tools": [],
              "resposta_especialista": {"dominio": "faq", "intencao": "informar", "resposta": "",
                                        "fontes_a_mostrar": [{"texto": "Doc.", "fonte": "a.md"}]}}
    with patch.dict("os.environ", {"VENUS_TOM_LLM": "1", "VENUS_RESPOSTA_SO_DAS_TOOLS": "faq"}), \
            patch("venus_sdk.nodes.orquestrador.get_llm_orquestrador") as tom:
        texto = no_orquestrador(estado)["resposta_final"]
    tom.assert_not_called()
    assert texto.endswith("Doc.\n\nFonte: a.md")


def test_frase_sobre_alergia_precisa_ficar_igual():
    from venus_sdk.nodes.orquestrador import _reescrita_fiel

    original = "Sua rotina: A.\n\nVocê não declarou nenhuma alergia no seu cadastro."
    assert not _reescrita_fiel(original, "Sua rotina: A. Nada de alergia pra se preocupar!", {})
    assert _reescrita_fiel(original, "Oii! Sua rotina: A.\n\nVocê não declarou nenhuma alergia no seu cadastro.", {})

