"""Camada 1 do guardrail (regex) contra um conjunto fixo de frases.

`fixtures/injecoes.txt` e `fixtures/legitimas.txt` têm uma frase por linha.
Meta: 100% das legítimas liberadas e pelo menos 90% das injeções bloqueadas
só pela regex; o que escapa fica para a camada 2 (classificador LLM).
Para acrescentar um caso, basta uma linha nova no arquivo.
"""

from pathlib import Path

import pytest

from venus_sdk.guardrail_rules import guardrail_entrada

FIXTURES = Path(__file__).parent / "fixtures"
META_INJECOES_BLOQUEADAS = 0.9


MARCA_CAMADA2 = "# camada2"


def _frases(arquivo: str) -> list[str]:
    """Frases do arquivo, sem comentários e sem as marcadas `# camada2` (que só
    o classificador LLM pega — medidas em tests/manual/avaliar_guardrail.py)."""
    linhas = (FIXTURES / arquivo).read_text(encoding="utf-8").splitlines()
    return [linha.strip() for linha in linhas
            if linha.strip() and not linha.lstrip().startswith("#") and not linha.rstrip().endswith(MARCA_CAMADA2)]


INJECOES = _frases("injecoes.txt")
LEGITIMAS = _frases("legitimas.txt")


def test_conjuntos_tem_o_tamanho_minimo():
    assert len(INJECOES) >= 30 and len(LEGITIMAS) >= 30


@pytest.mark.parametrize("mensagem", LEGITIMAS)
def test_mensagem_legitima_e_liberada(mensagem):
    assert guardrail_entrada(mensagem) == (False, None)


def test_taxa_de_injecoes_bloqueadas_pela_regex():
    escaparam = [frase for frase in INJECOES if not guardrail_entrada(frase)[0]]
    taxa = 1 - len(escaparam) / len(INJECOES)
    print(f"\nInjeções bloqueadas pela camada 1: {taxa:.0%} ({len(INJECOES) - len(escaparam)}/{len(INJECOES)})")
    for frase in escaparam:
        print(f"  escapou: {frase}")
    assert taxa >= META_INJECOES_BLOQUEADAS, escaparam


@pytest.mark.parametrize("mensagem", INJECOES[:10])
def test_frases_das_revisoes_sao_bloqueadas(mensagem):
    # As 10 primeiras do arquivo vieram das revisões: nenhuma pode escapar.
    assert guardrail_entrada(mensagem)[0] is True
