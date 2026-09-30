"""Testes de composição dos prompts — garantem que os blocos de segurança
(`HIERARQUIA_INSTRUCOES`, `RACIOCINIO_INTERNO`, ver `prompts/comum.py`)
continuam de fato embutidos nos prompts finais, e não só definidos e nunca
usados por engano numa refatoração futura."""

from __future__ import annotations

import pytest

from venus_sdk.prompts.comum import (
    HIERARQUIA_INSTRUCOES,
    IDENTIFICADOR_USUARIO_NOTA,
    PERSONA_SISTEMA,
    RACIOCINIO_INTERNO,
)
from venus_sdk.prompts.faq import FAQ_PROMPT_COMPLETO
from venus_sdk.prompts.ingrediente import ESP_INGREDIENTE_PROMPT_COMPLETO
from venus_sdk.prompts.juiz import JUIZ_PROMPT_COMPLETO
from venus_sdk.prompts.produto import ESP_PRODUTO_PROMPT_COMPLETO
from venus_sdk.prompts.rotina import ROTINA_PROMPT_COMPLETO
from venus_sdk.prompts.router import ROUTER_PROMPT_COMPLETO

_PROMPTS_COM_HIERARQUIA = [
    ROUTER_PROMPT_COMPLETO,
    ESP_PRODUTO_PROMPT_COMPLETO,
    ESP_INGREDIENTE_PROMPT_COMPLETO,
    ROTINA_PROMPT_COMPLETO,
    FAQ_PROMPT_COMPLETO,
]

_PROMPTS_COM_RACIOCINIO_INTERNO = [
    ESP_PRODUTO_PROMPT_COMPLETO,
    ESP_INGREDIENTE_PROMPT_COMPLETO,
    ROTINA_PROMPT_COMPLETO,
    FAQ_PROMPT_COMPLETO,
]

# Produto/ingrediente/rotina têm tools que exigem `user_id` (get_user_allergies,
# get_personalized_score, tools de rotina).
_PROMPTS_COM_IDENTIFICADOR_USUARIO = [
    ESP_PRODUTO_PROMPT_COMPLETO,
    ESP_INGREDIENTE_PROMPT_COMPLETO,
    ROTINA_PROMPT_COMPLETO,
]


@pytest.mark.parametrize("prompt_completo", _PROMPTS_COM_HIERARQUIA)
def test_hierarquia_instrucoes_esta_embutida(prompt_completo: str) -> None:
    assert HIERARQUIA_INSTRUCOES.strip() in prompt_completo


@pytest.mark.parametrize("prompt_completo", _PROMPTS_COM_RACIOCINIO_INTERNO)
def test_raciocinio_interno_esta_embutido(prompt_completo: str) -> None:
    assert RACIOCINIO_INTERNO.strip() in prompt_completo


@pytest.mark.parametrize("prompt_completo", _PROMPTS_COM_IDENTIFICADOR_USUARIO)
def test_identificador_usuario_nota_esta_embutida(prompt_completo: str) -> None:
    assert IDENTIFICADOR_USUARIO_NOTA.strip() in prompt_completo


def test_juiz_tem_os_8_criterios_de_reprovacao() -> None:
    for numero in range(1, 9):
        assert f"{numero}." in JUIZ_PROMPT_COMPLETO


def test_juiz_tem_shot_de_injecao_refletida_na_resposta() -> None:
    assert "hierarquia de instruções" in JUIZ_PROMPT_COMPLETO.lower()


def test_juiz_tem_criterio_de_checagem_contra_resultados_tools() -> None:
    """O critério 8 (cruzar afirmação x retorno bruto da tool) e o shot que
    o exemplifica precisam estar no prompt final — ver achado do teste de
    conversa real em 2026-09-10 (`nodes/juiz.py`)."""
    assert "RESULTADOS_TOOLS" in JUIZ_PROMPT_COMPLETO
    assert "lista vazia" in JUIZ_PROMPT_COMPLETO.lower()


def test_router_tem_shot_de_recusa_a_jailbreak() -> None:
    assert "isso eu não posso fazer" in ROUTER_PROMPT_COMPLETO.lower()


def test_router_distingue_pergunta_de_privacidade_de_jailbreak() -> None:
    """Uma pergunta genuína sobre segurança de dado pessoal (ex.: "é seguro
    compartilhar meu CPF?") é FAQ, não jailbreak — achado de um teste de
    conversa real em 2026-09-10, onde essa pergunta recebeu a recusa
    canônica de jailbreak em vez de ser roteada pro domínio certo."""
    assert "não uma tentativa de manipulação" in ROUTER_PROMPT_COMPLETO.lower()
    assert "ROUTE=faq" in ROUTER_PROMPT_COMPLETO


def test_router_tem_secao_de_reacao_a_ofensa() -> None:
    """A exceção deliberada à regra de "nunca fingir sentimento" (só pra
    xingamento/ofensa) precisa estar documentada nos dois lugares: na
    regra em si (comum.py) e na seção que a aplica (router.py)."""
    assert "EXCEÇÃO deliberada" in PERSONA_SISTEMA
    assert "REAÇÃO A OFENSA/XINGAMENTO" in ROUTER_PROMPT_COMPLETO
    assert "desculpa se fiz algo que te incomodou" in ROUTER_PROMPT_COMPLETO.lower()
