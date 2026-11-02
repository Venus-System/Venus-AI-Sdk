"""Avaliação do casamento alergia × ingrediente (`tests/manual/avaliar_alergias.py`):
não usa LLM nem banco, então roda aqui e falha com qualquer erro."""

import avaliar_alergias


def test_casos_escritos_a_mao_sem_erro():
    relatorio = avaliar_alergias.avaliar(avaliar_alergias.carregar())
    assert relatorio["perdidos"] == [] and relatorio["falsos"] == [] and relatorio["alerta_errado"] == []


def test_catalogo_de_producao_sem_erro():
    # As 18 alergias de venus.allergies × os ingredientes usados em produtos, com
    # gabarito escrito sem `alergias.py`. Antes da correção de 2026-10-30: 53/71
    # conflitos achados e 5 falsos (Parfum, propilenoglicol, metossulfato...).
    catalogo = avaliar_alergias.avaliar_catalogo()
    assert catalogo["pares"] > 9000
    assert catalogo["perdidos"] == [] and catalogo["falsos"] == []


def test_intervalo_de_confianca():
    baixo, alto = avaliar_alergias.intervalo(47, 47)
    assert alto == 1.0 and 0.92 < baixo < 0.93


def test_script_imprime_a_tabela(capsys):
    assert avaliar_alergias.main([]) == 0
    saida = capsys.readouterr().out
    assert "Conflitos achados (recall)" in saida and "IC 95%" in saida and "Catálogo de produção" in saida
