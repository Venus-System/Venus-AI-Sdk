"""O conjunto de `tests/manual/avaliar_fontes.py` está bem formado e as conferências
sem LLM funcionam. O resultado com o LLM de verdade fica em `docs/custo-e-roi.md`."""

from collections import Counter

import avaliar_fontes


def test_conjunto_bem_formado():
    casos = avaliar_fontes.carregar()
    perguntas = [caso["pergunta"] for caso in casos]
    assert len(perguntas) == len(set(perguntas))
    assert set(Counter(caso["tipo"] for caso in casos)) == set(avaliar_fontes.TIPOS)
    assert all(isinstance(caso["nota"], int) for caso in casos if caso["tipo"] == "nota")
    assert all(isinstance(caso["tem_fragrancia"], bool) for caso in casos if caso["tipo"] == "fragrancia")


def test_nota_conhecida():
    caso = {"tipo": "nota", "nota": 63}
    assert avaliar_fontes.acertou_conhecida(caso, "A nota geral é 63/100.")
    assert not avaliar_fontes.acertou_conhecida(caso, "A nota geral é 163.")
    assert not avaliar_fontes.acertou_conhecida(caso, "A nota de saúde é 85.")


def test_fragrancia_conhecida():
    tem = {"tipo": "fragrancia", "tem_fragrancia": True}
    nao_tem = {"tipo": "fragrancia", "tem_fragrancia": False}
    assert avaliar_fontes.acertou_conhecida(tem, "Sim, ele tem fragrância na fórmula.")
    assert not avaliar_fontes.acertou_conhecida(tem, "Ele tem 22 ingredientes cadastrados.")
    assert not avaliar_fontes.acertou_conhecida(tem, "Ele não tem fragrância.")
    assert avaliar_fontes.acertou_conhecida(nao_tem, "Ele não tem fragrância na lista.")


def test_le_a_saida_do_auditor():
    saida = '{"afirmacoes": [{"texto": "x", "sustentada": false, "motivo": "y"}]}'
    assert avaliar_fontes.ler_auditoria(saida)[0]["sustentada"] is False
    assert avaliar_fontes.ler_auditoria("sem json") == []


def test_resumo_lista_o_que_foi_marcado():
    resultados = [
        {"tipo": "armadilha", "pergunta": "p1", "resposta": "r", "acertou": None,
         "afirmacoes": [{"texto": "espere 10 minutos", "sustentada": False}]},
        {"tipo": "nota", "pergunta": "p2", "resposta": "r", "acertou": True, "afirmacoes": []},
        {"tipo": "aberta", "pergunta": "p3", "resposta": "r", "acertou": None, "afirmacoes": None},
    ]
    texto = avaliar_fontes.resumo(resultados)
    assert "espere 10 minutos" in texto and "| Respostas sem afirmação sem fonte | 1/2" in texto
    assert "| Não auditadas (falha do auditor) | 1 |" in texto


def test_dizer_que_nao_tem_o_dado_nao_conta_como_acerto():
    caso = {"tipo": "fragrancia", "tem_fragrancia": False}
    assert not avaliar_fontes.acertou_conhecida(caso, "Não tenho um dado cadastrado que responda exatamente a isso.")
    assert avaliar_fontes.acertou_conhecida(caso, "Não: fragrância não aparece nos 24 ingredientes cadastrados.")
