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

from venus_sdk.llm.models import extrair_texto_resposta, get_llm_rapido


def test_get_llm_rapido_limita_o_raciocinio_interno() -> None:
    get_llm_rapido.cache_clear()
    try:
        with patch("venus_sdk.llm.models.ChatGroq") as chat_groq_mock:
            get_llm_rapido()
    finally:
        get_llm_rapido.cache_clear()

    _, kwargs = chat_groq_mock.call_args
    assert kwargs["reasoning_effort"] == "low"
    assert kwargs["max_tokens"] == 1024


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
