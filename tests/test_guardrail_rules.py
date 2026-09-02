"""Testes das regras puras de guardrail (guardrail_rules.py)."""

from __future__ import annotations

import pytest

from venus_sdk.guardrail_rules import guardrail_saida, remover_emojis


@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("Oi! 👋 Como posso ajudar?", "Oi! Como posso ajudar?"),
        (
            "com toda a modéstia que um assistente de skincare consegue ter 💅. Time 🏆✨!",
            "com toda a modéstia que um assistente de skincare consegue ter. Time!",
        ),
        ("sem nenhum emoji aqui", "sem nenhum emoji aqui"),
        ("", ""),
        (None, ""),
        # blocos fora do range original (Arrows, Misc Technical, Geometric
        # Shapes) e a marca de keycap combinante — ver achado do code-review.
        ("não esqueça do protetor solar ⏰", "não esqueça do protetor solar"),
        ("dormiu bem ⌚?", "dormiu bem?"),
        ("assista o vídeo ▶️ antes de aplicar", "assista o vídeo antes de aplicar"),
        ("dia ↔️ noite, use produtos diferentes", "dia noite, use produtos diferentes"),
        ("faça isso 1️⃣ antes de dormir", "faça isso 1 antes de dormir"),
    ],
)
def test_remover_emojis(entrada: str | None, esperado: str) -> None:
    assert remover_emojis(entrada) == esperado  # type: ignore[arg-type]


def test_guardrail_saida_aprova_apos_remover_emoji() -> None:
    """A resposta sanitizada (sem emoji) não deve ser bloqueada por si só."""
    texto_sanitizado = remover_emojis("Oi, tudo bem? 👋")
    bloqueado, motivo = guardrail_saida(texto_sanitizado)

    assert bloqueado is False
    assert motivo is None
