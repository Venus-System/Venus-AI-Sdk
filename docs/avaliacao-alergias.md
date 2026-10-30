# Avaliação das alergias

Resultado de `tests/manual/avaliar_alergias.py` sobre duas bases:
- **Casos escritos à mão** (`tests/fixtures/avaliacao_alergias.jsonl`): 60 pares
  alergia × ingrediente (37 com conflito de verdade, 23 sem), com INCI e nome comum,
  escolhidos para serem difíceis.
- **Catálogo de produção** (`tests/fixtures/avaliacao_alergias_catalogo.json`): as 18
  alergias de `venus.allergies` × os 515 ingredientes usados em produtos, 9.270 pares.
  O gabarito diz quais ingredientes conflitam com cada alergia e foi escrito por regras
  à parte, por INCI, sem usar `alergias.py`.

**O que é medido:**
- **Casamento:** `venus_sdk/alergias.py` (`termos_da_alergia` + `termos_em_conflito`),
  a regra única usada por `suggest_routine`, pelo Juiz e pelo Orquestrador.
- **Alerta ponta a ponta:** o Orquestrador recebe do LLM um texto que não fala da
  alergia, e a resposta final tem que trazer o alerta fixo (`_alertas_de_alergia`)
  quando há conflito, e só nesse caso.

Não usa LLM nem banco, então roda no `pytest` (`tests/nodes/test_avaliacao_alergias.py`).
O teste falha se aparecer um erro nos casos fora das lacunas marcadas
`"lacuna_conhecida": true`, ou qualquer erro no catálogo.

Cada taxa vem com o intervalo de confiança de 95% (Wilson). Com amostra pequena, 100%
de acerto não quer dizer erro zero: com 47 de 47, a taxa real de erro ainda pode
chegar a uns 7%.

## Como rodar

```bash
python tests/manual/avaliar_alergias.py
python tests/manual/avaliar_alergias.py --saida-markdown r.md
```

## Resultado: 2026-10-30, com as alergias do catálogo de produção

Código: branch `fix/rotina-sugestao-alergia`, sobre `4f311d2`.

A avaliação anterior usava os nomes de alergia do seed de desenvolvimento ("Fragrância
(Parfum)", "Nozes / Karité"). O catálogo de produção tem outras 18, algumas em
inglês ("Parfum", "Limonene", "Phenoxyethanol"), e o casamento com elas tinha lacunas
que a base escrita à mão não pegava.

| Catálogo de produção (9.270 pares) | Antes | Depois |
|---|---|---|
| Conflitos achados (recall) | 75% (53/71) | **100% (71/71; IC 95%: 94,9% a 100%)** |
| Falsos conflitos | 5 | **0 (IC 95% da taxa: 0% a 0,04%)** |

| Casos escritos à mão (60 pares) | Resultado |
|---|---|
| Conflitos achados (recall) | 100% (37/37; IC 95%: 90,6% a 100%) |
| Sem conflito e sem alerta | 100% (23/23; IC 95%: 85,7% a 100%) |
| Resposta final com o alerta certo | 100% (60/60; IC 95%: 94,0% a 100%) |

**O que estava errado no catálogo:**
- **"Parfum"** só casava com "parfum": perdia os 13 alérgenos de fragrância que aparecem
  nos produtos (limoneno, linalol, geraniol, cumarina...). Agora "Parfum", "Fragrância" e
  "Perfume" usam o mesmo conjunto.
- **"Propilenoglicol"** não casava com PROPYLENE GLYCOL, porque o nome comum no
  catálogo é "PROPILENO GLICOL", com espaço. E casava por engano com o PPG
  ("polipropilenoglicol"), que é outra molécula.
- **"Sulfatos"** casava com metossulfato (condicionador) e tiossulfato (sal).
- **"Ácido salicílico"** não pegava o homossalato, filtro salicilato sem "salicyl" no INCI.
- **"Alérgenos de Fragrância (UE)"** não pegava o benzyl alcohol, que está na lista da UE.
- **"Phenoxyethanol"** não casava com o ingrediente cadastrado como "FENOXIETANOL" no
  próprio campo INCI.

**Sem exposição hoje:** formaldeído (e liberadores), parabenos, látex e metais não
aparecem em nenhum ingrediente usado em produtos. Os sinônimos de formaldeído e dos
liberadores (DMDM hydantoin, imidazolidinyl urea...) já estão cobertos para quando
aparecerem.

**Aliases dos ingredientes:** o catálogo tem 34.860 aliases para 8.237 ingredientes, mas
o casamento usa só o INCI e o nome comum. Nos ingredientes usados em produtos, os aliases
trariam 2 casos reais e vários falsos ("Natrocitral" → citrato de sódio; "methyl
sulfate" no nome químico de corantes; "isojasmonate" contém "soja"). O único caso real
relevante, o homossalato, entrou como sinônimo.

**Nomes digitados pelo usuário:** não entram no casamento. A alergia vem da lista fixa
do cadastro (`venus.allergies`) e o ingrediente vem do catálogo curado, então erro de
digitação não afeta o alerta. O que o usuário escreve no chat só define a pergunta.

**Limite:** o gabarito do catálogo segue uma política conservadora (ésteres de
propilenoglicol contam como conflito; salicilatos usados como filtro contam para
"Ácido salicílico"). Outra política mudaria os números, e o fixture deixa explícito o
que foi decidido.

## Resultado anterior: 2026-10-30, depois da primeira correção

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
