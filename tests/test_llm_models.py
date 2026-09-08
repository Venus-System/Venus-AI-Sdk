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

from unittest.mock import patch

from venus_sdk.llm.models import get_llm_rapido


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
