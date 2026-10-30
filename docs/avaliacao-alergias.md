# Avaliação das alergias

Resultado de `tests/manual/avaliar_alergias.py` sobre
`tests/fixtures/avaliacao_alergias.jsonl`: 34 pares alergia × ingrediente (22 com
conflito de verdade, 12 sem), com INCI e nome comum.

**O que é medido:**
- **Casamento:** `venus_sdk/alergias.py` (`termos_da_alergia` + `termos_em_conflito`),
  a mesma regra que `suggest_routine`, o Juiz e o Orquestrador usam.
- **Alerta ponta a ponta:** o Orquestrador recebe do LLM um texto que não fala da
  alergia, e a resposta final tem que trazer o alerta fixo (`_alertas_de_alergia`)
  quando há conflito, e só nesse caso.

Não usa LLM nem banco, então roda no `pytest` (`tests/nodes/test_avaliacao_alergias.py`).
O teste falha se aparecer um erro fora das lacunas marcadas `"lacuna_conhecida": true`,
e também quando uma lacuna é corrigida, para o conjunto ser atualizado junto.

## Como rodar

```bash
python tests/manual/avaliar_alergias.py
python tests/manual/avaliar_alergias.py --saida-markdown r.md
```

## Resultado: 2026-10-30

Código: branch `fix/rotina-sugestao-alergia`, sobre `ac1ff48`.

| Medida | Resultado |
|---|---|
| Conflitos achados (recall) | 73% (16/22) |
| Falsos conflitos | 1/12 |
| Resposta final com o alerta certo | 27/34 |

O alerta ponta a ponta erra exatamente nos 7 casos em que o casamento erra: o
Orquestrador não perde nenhum alerta que o casamento achou.

**Conflitos perdidos (lacunas conhecidas):**
- Fragrância (Parfum) × Limoneno e Linalol: alérgenos de fragrância da lista da UE,
  declarados no INCI pelo nome, não como "parfum".
- Ácido Salicílico (salicilatos) × Salicilato de sódio e Salicilato de betaína: o termo
  "salicilatos" (plural) não casa com "salicilato".
- Nozes / Karité × Óleo de amêndoas doces e Óleo de macadâmia: oleaginosas que não
  contêm a palavra "nozes".

**Falso conflito (lacuna conhecida):**
- Sulfato × Sulfato de magnésio: é um sal, não o surfactante (lauril sulfato) que quem
  evita sulfato quer evitar.

**O que funciona:** fragrância declarada como PARFUM, FRAGRANCE ou AROMA; ácido
salicílico; karité e nozes; álcool etílico, desnaturado e isopropílico, sem confundir
com álcoois graxos (cetearílico, cetílico), álcool benzílico e fenoxietanol; sulfatos
surfactantes; silicones; parabenos.
