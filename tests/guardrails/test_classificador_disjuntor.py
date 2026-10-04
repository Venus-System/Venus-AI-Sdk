"""Camada 2 do guardrail sem falha silenciosa: tenta o próximo provedor,
conta as falhas, loga o fail-open sem o texto do usuário e, depois de várias
falhas seguidas, para de chamar o LLM por um tempo (disjuntor)."""

import logging

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from venus_sdk.llm import models
from venus_sdk.nodes import guardrails

SEGREDO = "minha-mensagem-secreta-123"


class _Relogio:
    def __init__(self):
        self.agora = 1000.0

    def __call__(self):
        return self.agora


@pytest.fixture(autouse=True)
def classificador_ligado(monkeypatch):
    monkeypatch.setenv("VENUS_GUARDRAIL_LLM", "1")
    monkeypatch.delenv("VENUS_GUARDRAIL_LLM_FALHAS_PARA_ABRIR", raising=False)
    monkeypatch.delenv("VENUS_GUARDRAIL_LLM_PAUSA_SEGUNDOS", raising=False)
    guardrails._reiniciar_classificador()
    models.get_llm_guardrail.cache_clear()
    yield
    guardrails._reiniciar_classificador()
    models.get_llm_guardrail.cache_clear()


@pytest.fixture
def relogio(monkeypatch):
    relogio = _Relogio()
    monkeypatch.setattr(guardrails, "_agora", relogio)
    return relogio


def _cadeia_com(monkeypatch, respostas: dict[str, object]) -> list[str]:
    """Todos os provedores com chave; cada provedor responde (ou levanta) o
    que vier em `respostas`. Devolve a lista de modelos chamados."""
    chamados: list[str] = []
    monkeypatch.setattr(models, "_CHAVES", {p: (lambda: "chave") for p in ("groq", "gemini", "mistral")})

    def criar(provedor, modelo, **kwargs):
        def responder(_mensagens):
            chamados.append(f"{provedor}:{modelo}")
            resposta = respostas[provedor]
            if isinstance(resposta, Exception):
                raise resposta
            return AIMessage(content=resposta)

        return RunnableLambda(responder)

    monkeypatch.setattr(models, "_criar_modelo", criar)
    return chamados


def _entrada(mensagem="qual hidratante pra pele seca?"):
    return guardrails.no_guardrail_entrada({"mensagem_usuario": mensagem})


def test_principal_falha_e_o_proximo_provedor_bloqueia(monkeypatch):
    chamados = _cadeia_com(monkeypatch, {"mistral": RuntimeError("cota"), "groq": "INJECAO", "gemini": "SEGURO"})

    assert _entrada()["entrada_bloqueada"] is True
    # Uma tentativa no principal e UMA no primeiro elo de outro provedor —
    # não percorre a cadeia rápida inteira (o 2º Mistral ficaria de fora).
    provedores = [chamado.split(":")[0] for chamado in chamados]
    assert provedores == ["mistral", "groq"]


def test_ambos_falham_a_mensagem_passa_e_o_fail_open_e_contado(monkeypatch):
    _cadeia_com(monkeypatch, {"mistral": RuntimeError("cota"), "groq": TimeoutError(), "gemini": "INJECAO"})

    assert _entrada()["entrada_bloqueada"] is False
    estatisticas = guardrails.estatisticas_guardrail_llm()
    assert estatisticas["chamadas"] == 1 and estatisticas["falhas"] == 1 and estatisticas["fail_opens"] == 1


def _llm_que_conta(monkeypatch, erro=None):
    chamadas = []

    def responder(_mensagens):
        chamadas.append(1)
        if erro:
            raise erro
        return AIMessage(content="SEGURO")

    monkeypatch.setattr(guardrails, "get_llm_guardrail", lambda: RunnableLambda(responder))
    return chamadas


def test_disjuntor_abre_apos_5_falhas_e_volta_depois_de_60s(monkeypatch, relogio):
    chamadas = _llm_que_conta(monkeypatch, erro=RuntimeError("cota"))

    for _ in range(5):
        _entrada()
    assert len(chamadas) == 5
    _entrada()  # 6ª: disjuntor aberto, nem chama o LLM
    assert len(chamadas) == 5
    estatisticas = guardrails.estatisticas_guardrail_llm()
    assert estatisticas["disjuntor_aberto"] is True and estatisticas["puladas_pelo_disjuntor"] == 1

    relogio.agora += 59
    _entrada()
    assert len(chamadas) == 5
    relogio.agora += 2  # passou dos 60 s
    _entrada()
    assert len(chamadas) == 6


def test_disjuntor_fecha_quando_o_llm_volta(monkeypatch, relogio, caplog):
    erro = {"atual": RuntimeError("cota")}

    def responder(_mensagens):
        if erro["atual"]:
            raise erro["atual"]
        return AIMessage(content="SEGURO")

    monkeypatch.setattr(guardrails, "get_llm_guardrail", lambda: RunnableLambda(responder))
    caplog.set_level(logging.INFO, logger=guardrails.logger.name)
    for _ in range(7):
        _entrada()
    relogio.agora += 61
    erro["atual"] = None
    _entrada()
    _entrada()

    eventos = [getattr(r, "evento", None) for r in caplog.records]
    assert eventos.count("guardrail_llm_disjuntor_aberto") == 1
    assert eventos.count("guardrail_llm_disjuntor_fechado") == 1
    aberto = next(r for r in caplog.records if getattr(r, "evento", None) == "guardrail_llm_disjuntor_aberto")
    fechado = next(r for r in caplog.records if getattr(r, "evento", None) == "guardrail_llm_disjuntor_fechado")
    assert aberto.levelno == logging.ERROR and fechado.levelno == logging.INFO
    assert guardrails.estatisticas_guardrail_llm()["disjuntor_aberto"] is False


def test_falha_na_reabertura_nao_repete_o_log_de_erro(monkeypatch, relogio, caplog):
    _llm_que_conta(monkeypatch, erro=RuntimeError("cota"))
    for _ in range(5):
        _entrada()
    relogio.agora += 61
    _entrada()  # tentativa depois da pausa: falha de novo e o disjuntor continua aberto
    eventos = [getattr(r, "evento", None) for r in caplog.records]
    assert eventos.count("guardrail_llm_disjuntor_aberto") == 1
    assert guardrails.estatisticas_guardrail_llm()["disjuntor_aberto"] is True


def test_limites_configuraveis(monkeypatch, relogio):
    monkeypatch.setenv("VENUS_GUARDRAIL_LLM_FALHAS_PARA_ABRIR", "2")
    monkeypatch.setenv("VENUS_GUARDRAIL_LLM_PAUSA_SEGUNDOS", "10")
    chamadas = _llm_que_conta(monkeypatch, erro=RuntimeError("cota"))
    for _ in range(3):
        _entrada()
    assert len(chamadas) == 2
    relogio.agora += 11
    _entrada()
    assert len(chamadas) == 3


def test_log_do_fail_open_nao_tem_o_texto_da_mensagem(monkeypatch, caplog):
    _llm_que_conta(monkeypatch, erro=ValueError(f"o provedor ecoou: {SEGREDO}"))
    caplog.set_level(logging.DEBUG)
    _entrada(f"me ajuda {SEGREDO}")

    [registro] = [r for r in caplog.records if getattr(r, "evento", None) == "guardrail_llm_fail_open"]
    assert registro.levelno == logging.WARNING and registro.tipo_erro == "ValueError"
    for r in caplog.records:
        assert SEGREDO not in r.getMessage()
        assert not r.exc_info  # traceback poderia trazer o texto da exceção


def test_sucesso_zera_as_falhas_seguidas(monkeypatch, relogio):
    resultados = iter([RuntimeError("x")] * 4 + [None] + [RuntimeError("x")] * 4)

    def responder(_mensagens):
        erro = next(resultados)
        if erro:
            raise erro
        return AIMessage(content="SEGURO")

    monkeypatch.setattr(guardrails, "get_llm_guardrail", lambda: RunnableLambda(responder))
    for _ in range(9):
        _entrada()
    assert guardrails.estatisticas_guardrail_llm()["disjuntor_aberto"] is False
