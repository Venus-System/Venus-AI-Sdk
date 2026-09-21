"""Teste de regressão pro bug do gpt-oss-20b devolvendo `content=""`.

Reproduzido de verdade contra o Groq: sem limitar o raciocínio interno, o
modelo às vezes gasta o budget inteiro de tokens "pensando" e nunca chega a
escrever a resposta (`finish_reason="length"`, ~2046 de ~2048 tokens em
`reasoning_tokens`) — mesmo pra entrada simples como "eu te amo". Isso fazia
`no_roteador` cair no fallback genérico (`_RESPOSTA_DIRETA_FALLBACK`), sem
nenhuma relação com o que o usuário disse. `reasoning_effort="low"` +
`max_tokens` resolveram na prática (ver `llm/models.py::get_llm_rapido`).

Mocka `ChatGroq` (não chama a API de verdade, nem exige `GROQ_API_KEY` —
mesmo padrão dos outros testes de nós) — só confere que os parâmetros que
mitigam o bug continuam sendo passados, pra uma reversão futura não passar
despercebida."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from venus_sdk.llm import models as M
from venus_sdk.llm.models import extrair_texto_resposta, get_llm_especialista, get_llm_rapido, provedor_principal


def test_get_llm_rapido_limita_o_raciocinio_interno(monkeypatch) -> None:
    monkeypatch.setattr(M, "GROQ_API_KEY", "k")
    monkeypatch.setattr(M, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(M, "MISTRAL_API_KEY", None)  # independe das chaves do ambiente
    monkeypatch.delenv("LLM_CADEIA_RAPIDO", raising=False)
    get_llm_rapido.cache_clear()
    try:
        with patch("venus_sdk.llm.models.ChatGroq") as chat_groq_mock, patch("venus_sdk.llm.models.ChatGoogleGenerativeAI"):
            get_llm_rapido()
    finally:
        get_llm_rapido.cache_clear()

    chamadas = {c.kwargs["model"]: c.kwargs for c in chat_groq_mock.call_args_list}
    assert chamadas["openai/gpt-oss-20b"]["reasoning_effort"] == "low"
    assert chamadas["openai/gpt-oss-20b"]["max_tokens"] == 1024
    assert "openai/gpt-oss-120b" in chamadas


# --- extrair_texto_resposta ---
#
# Regressão pro bug de verdade rodando o grafo completo em 2026-09-08: o
# gemini-3.6-flash (usado por get_llm_especialista/get_llm_gemini) devolve
# `content` como uma LISTA de blocos com "thought signature" em vez da
# string simples que gemini-2.5-flash devolvia — `.strip()`/`json.loads()`
# direto nisso quebrava com AttributeError/TypeError em orquestrador.py e
# descartava a resposta de verdade do especialista em especialistas.py.


def test_extrair_texto_resposta_com_content_string() -> None:
    """Formato antigo (a maioria dos modelos) — passa direto."""
    assert extrair_texto_resposta(SimpleNamespace(content="RESULTADO=aprovado")) == "RESULTADO=aprovado"


def test_extrair_texto_resposta_com_content_lista_de_blocos() -> None:
    """Formato do gemini-3.6-flash: lista de blocos, com metadado de
    assinatura misturado — extrai só o texto e ignora o resto."""
    resposta = SimpleNamespace(
        content=[
            {"type": "text", "text": '{"dominio":"produto"}', "extras": {"signature": "abc123"}},
        ]
    )
    assert extrair_texto_resposta(resposta) == '{"dominio":"produto"}'


def test_extrair_texto_resposta_concatena_varios_blocos_de_texto() -> None:
    resposta = SimpleNamespace(content=[{"text": "parte 1 "}, {"text": "parte 2"}])
    assert extrair_texto_resposta(resposta) == "parte 1 parte 2"


def test_extrair_texto_resposta_ignora_bloco_sem_texto() -> None:
    resposta = SimpleNamespace(content=[{"type": "signature", "extras": {}}, {"text": "resposta real"}])
    assert extrair_texto_resposta(resposta) == "resposta real"


def test_extrair_texto_resposta_content_vazio() -> None:
    assert extrair_texto_resposta(SimpleNamespace(content="")) == ""
    assert extrair_texto_resposta(SimpleNamespace(content=None)) == ""
    assert extrair_texto_resposta(SimpleNamespace(content=[])) == ""


# --- provedor principal (cota do Gemini gratuito: 20 req/dia) ---


def test_provedor_principal_padrao_e_groq(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert provedor_principal() == "groq"
    monkeypatch.setenv("LLM_PROVIDER", " Gemini ")
    assert provedor_principal() == "gemini"


def test_parse_cadeia() -> None:
    assert M.parse_cadeia(" groq:a/b , Gemini:c ,,") == [("groq", "a/b"), ("gemini", "c")]
    for ruim in ("groq", "groq:", "openai:x", ":x"):
        with pytest.raises(ValueError):
            M.parse_cadeia(ruim)


def test_cadeia_pula_provedor_sem_chave_e_repetidos(monkeypatch) -> None:
    monkeypatch.setattr(M, "GROQ_API_KEY", "k")
    monkeypatch.setattr(M, "GEMINI_API_KEY", None)
    monkeypatch.setenv("LLM_CADEIA_ESPECIALISTA", "gemini:g1,groq:a,groq:a,groq:b")
    assert M.cadeia_especialista() == [("groq", "a"), ("groq", "b")]


def test_cadeia_padrao_depende_do_provedor_principal(monkeypatch) -> None:
    monkeypatch.setattr(M, "GROQ_API_KEY", "k")
    monkeypatch.setattr(M, "GEMINI_API_KEY", "k")
    monkeypatch.delenv("LLM_CADEIA_ESPECIALISTA", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    assert M.cadeia_especialista()[0][0] == "groq" and len(M.cadeia_especialista()) >= 3
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    assert M.cadeia_especialista()[0][0] == "gemini"


def test_cadeia_monta_varios_fallbacks_em_ordem(monkeypatch) -> None:
    monkeypatch.setattr(M, "GROQ_API_KEY", "k")
    monkeypatch.setattr(M, "GEMINI_API_KEY", "k")
    monkeypatch.setenv("LLM_CADEIA_ESPECIALISTA", "groq:a,gemini:b,groq:c,gemini:d")
    criados = []

    class _LLM:
        def __init__(self, nome): self.nome = nome
        def with_fallbacks(self, fbs): return ("cadeia", self.nome, [f.nome for f in fbs])

    def _fake(provedor, modelo, **kw):
        criados.append((provedor, modelo)); return _LLM(f"{provedor}:{modelo}")

    get_llm_especialista.cache_clear()
    with patch.object(M, "_criar_modelo", side_effect=_fake):
        r = get_llm_especialista()
    get_llm_especialista.cache_clear()
    assert r == ("cadeia", "groq:a", ["gemini:b", "groq:c", "gemini:d"])


def test_cadeia_sem_nenhuma_chave_falha_claro(monkeypatch) -> None:
    monkeypatch.setattr(M, "GROQ_API_KEY", None)
    monkeypatch.setattr(M, "GEMINI_API_KEY", None)
    monkeypatch.setattr(M, "MISTRAL_API_KEY", None)
    monkeypatch.delenv("LLM_CADEIA_ESPECIALISTA", raising=False)
    get_llm_especialista.cache_clear()
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        get_llm_especialista()
    get_llm_especialista.cache_clear()


def test_fallback_de_verdade_troca_de_modelo_quando_o_primeiro_falha() -> None:
    """Comportamento real do LangChain com a cadeia: 429 no 1º -> o 2º responde."""
    from langchain_core.messages import AIMessage
    from _fakes import LLMScript

    class _Quebrado(LLMScript):
        def _generate(self, *a, **k):
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

    ruim = _Quebrado(script=[AIMessage(content="x")])
    bom = LLMScript(script=[AIMessage(content="do segundo")])
    ruim2 = _Quebrado(script=[AIMessage(content="x")])
    cadeia = ruim.with_fallbacks([ruim2, bom])
    assert cadeia.invoke("oi").content == "do segundo"


def test_mistral_entra_na_cadeia_padrao_quando_ha_chave(monkeypatch) -> None:
    monkeypatch.setattr(M, "MISTRAL_API_KEY", "k")
    monkeypatch.setattr(M, "GROQ_API_KEY", None)
    monkeypatch.setattr(M, "GEMINI_API_KEY", None)
    monkeypatch.delenv("LLM_CADEIA_ESPECIALISTA", raising=False)
    assert M.cadeia_especialista()[0] == ("mistral", "ministral-14b-latest")
    assert all(p == "mistral" for p, _ in M.cadeia_especialista())


def test_mistral_sem_chave_e_pulada(monkeypatch) -> None:
    monkeypatch.setattr(M, "MISTRAL_API_KEY", None)
    monkeypatch.setattr(M, "GROQ_API_KEY", "k")
    monkeypatch.delenv("LLM_CADEIA_RAPIDO", raising=False)
    assert all(p != "mistral" for p, _ in M.cadeia_rapida())


def test_parse_cadeia_aceita_mistral() -> None:
    assert M.parse_cadeia("mistral:mistral-medium-latest") == [("mistral", "mistral-medium-latest")]
