# Avaliação das alergias

Resultado de `tests/manual/avaliar_alergias.py` sobre
`tests/fixtures/avaliacao_alergias.jsonl`: 47 pares alergia × ingrediente (29 com
conflito de verdade, 18 sem), com INCI e nome comum.

**O que é medido:**
- **Casamento:** `venus_sdk/alergias.py` (`termos_da_alergia` + `termos_em_conflito`),
  a regra única usada por `suggest_routine`, pelo Juiz e pelo Orquestrador.
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

## Resultado: 2026-10-30, depois da correção

Código: branch `fix/rotina-sugestao-alergia`, sobre `f67fbf3`.

| Medida | Antes (34 casos) | Depois (47 casos) |
|---|---|---|
| Conflitos achados (recall) | 73% (16/22) | **100% (29/29)** |
| Falsos conflitos | 1/12 | **0/18** |
| Resposta final com o alerta certo | 27/34 | **47/47** |

**O que mudou em `alergias.py`:**
- **Fragrância:** os alérgenos que a UE manda declarar pelo nome (limoneno, linalol,
  geraniol, cumarina, citral, eugenol e os demais) contam como fragrância. O benzyl
  alcohol fica de fora: está na lista, mas é usado sobretudo como conservante.
- **Salicilatos:** "salicilato" e "salicylate" casam, além do ácido salicílico. Antes,
  o plural "salicilatos" do cadastro não casava com "salicilato de sódio".
- **Nozes:** oleaginosas pelo nome comum e pelo gênero do INCI (amêndoa, macadâmia,
  avelã, caju, pecã, pistache, castanha-do-pará). Sem "castanha" (pegaria a
  castanha-da-índia) nem "caju" solto (está dentro de "cajuput").
- **Sulfato:** só o surfactante. Sal mineral de nome curto ("magnesium sulfate",
  "sulfato de sódio") não conta; "sodium lauryl sulfate" e "coco-sulfate" contam.

**Rotina com a mesma regra:** `suggest_routine` tinha uma cópia própria do casamento
que já tinha divergido. Ela excluía "benzyl alcohol" de quem declarou alergia a
álcool e não pegava salicilatos. Agora usa `alergias.py`
(`tests/regressao/test_rotina_regra_unica_de_alergia.py`).

**Casos novos (13):** depois da correção, o conjunto ganhou casos que não serviram
para escrever a regra. Eles testam sobretudo os falsos positivos que ela poderia criar:
- ácido cítrico × citral;
- óleo de coco e castanha-da-índia × nozes;
- cajepute × caju;
- sulfato de sódio;
- álcool benzílico × fragrância.

Todos passaram.

**Limite:** o conjunto ainda é pequeno e foi escrito à mão. Ingredientes fora dele
(outros extratos vegetais, nomes comerciais) podem não casar.
