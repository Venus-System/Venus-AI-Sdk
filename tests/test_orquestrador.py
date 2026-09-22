"""Testes do nó Orquestrador."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from venus_sdk.nodes.orquestrador import no_orquestrador


def _resposta_llm(texto: str) -> SimpleNamespace:
    return SimpleNamespace(content=texto)


def test_no_orquestrador_devolve_resposta_do_llm() -> None:
    texto_llm = "- Esse produto foi recomendado por causa do seu perfil.\n- *Recomendação*: use à noite."
    with patch("venus_sdk.nodes.orquestrador.get_llm_especialista") as get_llm_mock:
        get_llm_mock.return_value.invoke.return_value = _resposta_llm(texto_llm)
        resultado = no_orquestrador({"resposta_especialista": {"resposta": "ok"}})

    assert resultado["resposta_final"] == texto_llm
    get_llm_mock.return_value.invoke.assert_called_once()


def test_no_orquestrador_tenta_de_novo_quando_llm_devolve_vazio() -> None:
    texto_llm = "- Aqui está sua resposta.\n- *Recomendação*: siga o passo a passo."
    with patch("venus_sdk.nodes.orquestrador.get_llm_especialista") as get_llm_mock:
        get_llm_mock.return_value.invoke.side_effect = [
            _resposta_llm(""),
            _resposta_llm(texto_llm),
        ]
        resultado = no_orquestrador({"resposta_especialista": {"resposta": "ok"}})

    assert resultado["resposta_final"] == texto_llm
    assert get_llm_mock.return_value.invoke.call_count == 2


def test_no_orquestrador_usa_fallback_quando_llm_falha_duas_vezes() -> None:
    with patch("venus_sdk.nodes.orquestrador.get_llm_especialista") as get_llm_mock:
        get_llm_mock.return_value.invoke.side_effect = [
            _resposta_llm(""),
            _resposta_llm("   "),
        ]
        resultado = no_orquestrador({"resposta_especialista": {"resposta": "ok"}})

    assert resultado["resposta_final"]  # nunca vazio
    assert get_llm_mock.return_value.invoke.call_count == 2


def test_no_orquestrador_usa_fallback_quando_llm_levanta_excecao() -> None:
    """Gemini E o fallback Groq indisponíveis (ex.: cota estourada nos dois)
    não devem derrubar o `.ainvoke()` do grafo principal — só o caso de
    conteúdo vazio era tratado antes; uma exceção de verdade subia crua
    (achado de um teste de conversa real em 2026-09-10)."""
    with patch("venus_sdk.nodes.orquestrador.get_llm_especialista") as get_llm_mock:
        get_llm_mock.return_value.invoke.side_effect = RuntimeError("provedor indisponível")
        resultado = no_orquestrador({"resposta_especialista": {"resposta": "ok"}})

    assert resultado["resposta_final"]  # nunca vazio, mesmo com as duas tentativas falhando
    assert get_llm_mock.return_value.invoke.call_count == 2


def test_orquestrador_usa_conteudo_do_especialista_quando_llm_devolve_placeholder() -> None:
    from unittest.mock import patch

    from _fakes import LLMScript
    from langchain_core.messages import AIMessage

    from venus_sdk.nodes.orquestrador import no_orquestrador

    llm = LLMScript(script=[AIMessage(content="Oiiii, [nome]!!")])
    estado = {"resposta_especialista": {"dominio": "produto", "resposta": "Achei o Shampoo X da marca Y.",
                                        "recomendacao": "Use 2x por semana.", "fontes_usadas": ["search_product"]}}
    with patch("venus_sdk.nodes.orquestrador.get_llm_especialista", return_value=llm):
        r = no_orquestrador(estado)["resposta_final"]
    assert "Shampoo X" in r and "Use 2x por semana." in r and "[nome]" not in r


def test_orquestrador_nao_perde_passos_da_rotina() -> None:
    from unittest.mock import patch

    from _fakes import LLMScript
    from langchain_core.messages import AIMessage

    from venus_sdk.nodes.orquestrador import no_orquestrador

    llm = LLMScript(script=[AIMessage(content="Rotina criada com sucesso, pode usar sem medo!")])
    estado = {"resposta_especialista": {"dominio": "rotina", "recomendacao": "",
              "resposta": "Pronta. Passos (manha): 1) Gel X (Limpeza); 2) Creme Y (Hidratante).", "fontes_usadas": ["suggest_routine"]}}
    with patch("venus_sdk.nodes.orquestrador.get_llm_especialista", return_value=llm):
        r = no_orquestrador(estado)["resposta_final"]
    assert "Gel X" in r and "Creme Y" in r


def test_orquestrador_falha_tecnica_vira_mensagem_simples_sem_chamar_llm() -> None:
    from unittest.mock import patch

    from venus_sdk.nodes.orquestrador import no_orquestrador

    estado = {"resposta_especialista": {"dominio": "produto", "intencao": "erro_tecnico", "resposta": "Traceback...",
                                        "recomendacao": "", "fontes_usadas": []}, "aprovado_juiz": False}
    with patch("venus_sdk.nodes.orquestrador.get_llm_especialista", side_effect=AssertionError("não deve chamar o LLM")):
        r = no_orquestrador(estado)["resposta_final"]
    assert "probleminha" in r and "Traceback" not in r


def test_juiz_esgotado_em_produto_nao_repassa_texto_reprovado() -> None:
    import json
    from unittest.mock import patch

    from venus_sdk.nodes.orquestrador import no_orquestrador

    ev = [{"tool": "search_product", "resultado": json.dumps([{"product_id": 1, "name": "Shampoo X", "brand_name": "Lola"}])},
          {"tool": "get_product_score", "resultado": json.dumps({"encontrado": False})}]
    estado = {"aprovado_juiz": False, "evidencias_tools": ev,
              "resposta_especialista": {"dominio": "produto", "intencao": "sugerir", "recomendacao": "",
                                        "resposta": "Shampoo X tem karité e score 90/100", "fontes_usadas": ["search_product"]}}
    with patch("venus_sdk.nodes.orquestrador.get_llm_especialista", side_effect=AssertionError("sem LLM")):
        r = no_orquestrador(estado)["resposta_final"]
    assert "Shampoo X (Lola)" in r and "karité" not in r and "90" not in r


def test_juiz_esgotado_sem_evidencia_util_da_resposta_generica() -> None:
    from unittest.mock import patch

    from venus_sdk.nodes.orquestrador import no_orquestrador

    estado = {"aprovado_juiz": False, "evidencias_tools": [],
              "resposta_especialista": {"dominio": "ingrediente", "resposta": "inventado", "fontes_usadas": []}}
    with patch("venus_sdk.nodes.orquestrador.get_llm_especialista", side_effect=AssertionError("sem LLM")):
        r = no_orquestrador(estado)["resposta_final"]
    assert "inventado" not in r and "confirmar" in r
