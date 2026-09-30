# API de Classificação — Venus

## 1. O que a API faz

O app Venus permite que a pessoa escaneie um produto cosmético e descubra se ele combina com seu perfil.

A API de Classificação:

- recebe **um usuário e um produto**;
- calcula uma **nota de 0 a 100**;
- devolve a **lista de motivos** que explica a nota.

A explicação é parte importante do resultado: não basta informar a nota; o sistema deve conseguir mostrar por que aquele resultado aconteceu.

---

## 2. Como a nota de 0 a 100 é dividida

A nota final responde a duas perguntas diferentes:

| Parte | Pontos | O que mede | Código |
|---|---:|---|---|
| Qualidade do produto | 35 | Avaliação do produto em si, igual para todos | `baseScore` |
| Adequação ao perfil | 65 | Quanto o produto combina com aquela pessoa | `profileScore` |
| **Total** | **100** | Nota apresentada ao usuário | `finalScore` |

### Fórmula geral

```text
finalScore = baseScore + profileScore
```

A separação permite distinguir **qualidade do produto** de **compatibilidade com a pessoa**.

O `baseScore` pode ser calculado uma vez e reaproveitado para diferentes usuários. Já o `profileScore` precisa ser calculado de acordo com o perfil de cada pessoa.

---

# 3. `baseScore` — os 35 pontos de qualidade

O `baseScore` não depende do usuário. Ele é formado por quatro categorias:

| Categoria | Peso aproximado |
|---|---:|
| Saúde (`healthScore`) | 41,2% |
| Desempenho (`performanceScore`) | 23,5% |
| Ambiental (`environmentalScore`) | 17,6% |
| Ética (`ethicalScore`) | 17,6% |

Existe ainda o `transparencyScore`, mas ele é calculado separadamente e **não entra no `baseScore`**. Ele mede a qualidade do cadastro do produto.

Também existe o `confidenceScore`, usado internamente para acompanhar a confiança dos dados.

---

## 3.1 `healthScore`

A nota de saúde considera o risco de irritação e de entupimento dos poros.

### Cálculo do risco de cada ingrediente

```text
risco = (irritação + entopePoros) / 2
```

### Cálculo da nota

```text
healthScore = 100 × (1 - médiaDosRiscos / 10)
```

Quanto menor o risco médio dos ingredientes, maior a nota de saúde.

### Exemplo

Para o produto usado no documento:

```text
soma dos riscos = 6,5
quantidade de ingredientes = 6

média = 6,5 / 6
      = 1,08

healthScore = 100 × (1 - 1,08 / 10)
            ≈ 89
```

---

## 3.2 `environmentalScore`

A nota ambiental considera:

- características ambientais dos ingredientes;
- características da embalagem.

A divisão é:

- **70%** ingredientes;
- **30%** embalagem.

### Ingredientes

```text
nota ecológica = (biodegradabilidade + (10 - risco ambiental)) / 2
```

O `10 - risco ambiental` é usado porque risco ambiental alto é ruim, enquanto biodegradabilidade alta é boa.

### Embalagem

A embalagem pode receber:

```text
+25 se reciclável
+25 se refilável
+25 se biodegradável
+0,25 para cada 1% de material reciclado
```

### Fórmula final

```text
environmentalScore =
    (parteDosIngredientes × 0,7)
    +
    (parteDaEmbalagem × 0,3)
```

No exemplo do documento:

```text
parte dos ingredientes = 76,7
parte da embalagem = 32,5

environmentalScore =
    (76,7 × 0,7) + (32,5 × 0,3)
    ≈ 63
```

---

## 3.3 `ethicalScore`

A nota ética começa em 50, considerada neutra.

```text
ethicalScore = 50
```

Depois são acrescentados pontos:

```text
+20 se a marca declara ser vegana
+20 se a marca declara não testar em animais
+5 por selo ético verificado
```

O documento estabelece limite de 10 pontos para os selos.

### Exemplo

```text
50 + 20 + 20 + 5 = 95
```

Resultado:

```text
ethicalScore = 95
```

---

## 3.4 `performanceScore`

A nota de desempenho considera a quantidade de **efeitos/benefícios validados** dos ingredientes.

Cada ingrediente pode contribuir com no máximo 3 benefícios.

```text
benefício do ingrediente = quantidade de efeitos validados
                           limitado a 3
```

Depois:

```text
performanceScore =
    100 × médiaDosBenefícios / 3
```

No exemplo:

```text
Água                  = 0
Niacinamida           = 3
Glicerina             = 2
Ácido Hialurônico     = 2
Fenoxietanol          = 0
Fragrância            = 0

soma = 7
média = 7 / 6
     ≈ 1,17

performanceScore =
    100 × 1,17 / 3
    ≈ 39
```

Uma nota baixa aqui não significa necessariamente que o produto seja ruim: pode significar que o catálogo ainda possui poucos efeitos cadastrados.

---

## 3.5 `baseScore` final

As quatro notas são combinadas por uma **média ponderada**.

No exemplo:

| Nota | Valor | Peso | Contribuição |
|---|---:|---:|---:|
| Saúde | 89 | 0,412 | 36,6 |
| Desempenho | 39 | 0,235 | 9,2 |
| Ambiental | 63 | 0,176 | 11,1 |
| Ética | 95 | 0,176 | 16,8 |
| **Total** | | | **73,7** |

Como o `baseScore` representa 35 pontos:

```text
baseScore = 35 × (73,7 / 100)
          = 25,8
```

Portanto:

```text
baseScore = 25,8 de 35
```

---

# 4. `profileScore` — os 65 pontos de compatibilidade

Essa parte considera o perfil da pessoa.

O questionário possui **11 telas que resultam em 21 características utilizadas pelo cálculo**.

### Distribuição

| Grupo | Características | Pontos |
|---|---:|---:|
| Pele | 8 | 47 |
| Cabelo | 2 | 22 |
| Universais | 11 | 37 |

Nem todas as características entram em todos os produtos.

Por exemplo, características de cabelo não devem reduzir a pontuação de um produto exclusivamente facial.

---

## 4.1 Quanto vale cada característica

### Pele

| Característica | Pontos |
|---|---:|
| Tipo de pele | 12 |
| Sensibilidade | 10 |
| Tendência a acne | 7 |
| Rosácea | 5 |
| Eczema | 5 |
| Hiperpigmentação | 3 |
| Melasma | 3 |
| Fototipo | 2 |
| **Total** | **47** |

### Cabelo

| Característica | Pontos |
|---|---:|
| Tipo de cabelo | 11 |
| Tipo de couro cabeludo | 11 |
| **Total** | **22** |

### Universais

| Característica | Pontos |
|---|---:|
| Gestante | 6 |
| Amamentando | 5 |
| Faixa etária | 4 |
| Prefere sem fragrância | 4 |
| Prefere sem parabeno | 3 |
| Prefere sem sulfato | 3 |
| Prefere sem silicone | 3 |
| Prefere vegano | 3 |
| Gênero | 2 |
| Prefere cruelty-free | 2 |
| Prefere sustentável | 2 |
| **Total** | **37** |

Os valores ficam no código, no método `maxPoints()` de cada classe de pergunta.

---

## 4.2 Como uma pergunta recebe pontos

Cada pergunta começa com zero e recebe pontos conforme o sistema encontra regras relacionadas aos ingredientes do produto.

A régua utilizada é 20 pontos:

```text
notaDaPergunta =
    valorDaPergunta × (somaDosPontosDasRegras / 20)
```

A nota fica limitada entre:

```text
0
```

e

```text
valor máximo da pergunta
```

### Exemplo: tipo de pele

A pergunta vale 12 pontos.

```text
Nenhuma regra:
12 × (0 / 20) = 0

Niacinamida (+8):
12 × (8 / 20) = 4,8

Niacinamida + Ácido Salicílico (+14):
12 × (14 / 20) = 8,4

20 pontos ou mais:
12 × (20 / 20) = 12
```

---

## 4.3 Perguntas que não se aplicam

Uma pergunta sai do cálculo quando:

- nenhuma característica correspondente está ativa no perfil; ou
- nenhum ingrediente possui regra relacionada àquela característica.

Os pontos disponíveis são então redistribuídos:

```text
profileScore =
    65 × (pontosGanhos / pontosPossíveis)
```

Isso evita que uma pessoa perca pontos simplesmente porque determinada pergunta não faz sentido para aquele produto.

---

# 5. Exemplo completo

Usuária:

- pele oleosa;
- sensibilidade média;
- tendência a acne;
- sem rosácea;
- sem eczema;
- não está grávida;
- prefere produtos sem fragrância;
- prefere produtos veganos.

Produto:

**Sérum Facial de Niacinamida — Aurora**

Resultado das principais perguntas:

| Pergunta | Máximo | Pontos ganhos |
|---|---:|---:|
| Tipo de pele | 12 | 4,8 |
| Sensibilidade | 10 | 0 |
| Tendência a acne | 7 | 2,1 |
| Hiperpigmentação | 3 | 0,75 |
| Sem fragrância | 4 | 0 |
| Vegano | 3 | 3 |
| **Total** | **39 possíveis** | **10,65** |

### `profileScore`

```text
profileScore =
    65 × (10,65 / 39)

profileScore ≈ 17,8
```

### Compatibilidade

```text
compatibilityPercentage =
    100 × (10,65 / 39)

compatibilityPercentage ≈ 27,3%
```

---

# 6. `finalScore`

Agora as duas partes são somadas:

```text
baseScore    = 25,8
profileScore = 17,8
-------------------
finalScore   = 43,6
```

Arredondando:

```text
finalScore = 44
```

No exemplo do documento, a nota fica na faixa **Não recomendado**.

---

# 7. Travas de segurança

As alergias não funcionam como uma simples perda de pontos.

Elas funcionam como **travas** e podem substituir o resultado calculado.

| Situação | Resultado |
|---|---|
| Alergia grave/crítica | Nota 0 — contraindicado |
| Alergia alta | Nota máxima 15 — contraindicado |
| Alergia média | Nota máxima 49 — não recomendado |
| Alergia leve | Desconta 15 pontos e gera alerta |
| Regra de bloqueio (`BLOCK`) | Nota 0 — contraindicado |

### Ordem das travas

1. Regra de bloqueio (`BLOCK`)
2. Alergia grave
3. Alergia alta
4. Alergia média
5. Alergia leve
6. Nenhuma trava

O bloqueio sempre vence um bônus. Um benefício não compensa uma condição de segurança.

---

# 8. Faixas de recomendação

| Nota | Faixa |
|---:|---|
| 85–100 | `IDEAL` |
| 70–84 | `RECOMMENDED` |
| 50–69 | `ACCEPTABLE` |
| 25–49 | `NOT_RECOMMENDED` |
| 0–24 | `CONTRAINDICATED` |

---

# 9. Nível de risco

O `riskLevel` não é uma segunda nota. Ele representa **segurança**, enquanto o `recommendationLevel` representa a faixa da nota.

| Nível | Quando acontece |
|---|---|
| `CRITICAL` | Bloqueio ou alergia grave/alta |
| `HIGH` | Alergia média ou nota final abaixo de 25 |
| `MEDIUM` | Alergia leve, nota abaixo de 50 ou regra de alerta |
| `LOW` | Nenhuma das condições anteriores |

Assim, a recomendação e o risco podem representar informações diferentes.

```text
recommendationLevel → "como o produto foi avaliado?"
riskLevel            → "existe algum risco ou alerta?"
```

---

# 10. Como o código é organizado

A API utiliza três padrões principais.

## Strategy

O padrão **Strategy** separa diferentes formas de realizar um mesmo tipo de cálculo.

Exemplos:

```text
HealthScoreStrategy
EnvironmentalScoreStrategy
EthicalScoreStrategy
PerformanceScoreStrategy
TransparencyScoreStrategy
```

Também existem Strategies para as características do perfil.

A vantagem é evitar uma classe gigantesca cheia de `if/else`.

---

## Template Method

As 21 características do perfil seguem praticamente o mesmo fluxo:

1. descobrir quais características estão ativas;
2. procurar as regras relacionadas;
3. somar os pontos;
4. limitar ao máximo da pergunta.

O **Template Method** mantém esse fluxo em uma classe abstrata, enquanto cada pergunta define apenas a parte específica.

Exemplo simplificado:

```java
public abstract class AbstractProfileQuestionStrategy {

    public final QuestionScore score(
            ProductSnapshot produto,
            ResolvedProfile perfil) {

        Set<Long> tags = activeTagIds(perfil);

        if (tags.isEmpty()) {
            return QuestionScore.notApplicable(
                questionKey(),
                maxPoints()
            );
        }

        List<RuleHit> regras = produto.rulesFor(tags);

        return consolidate(
            questionKey(),
            maxPoints(),
            regras
        );
    }

    protected abstract Set<Long> activeTagIds(
        ResolvedProfile perfil
    );
}
```

---

## Registry

O Spring encontra automaticamente as classes marcadas com `@Component`.

O serviço recebe as estratégias por injeção:

```java
public ClassificationService(
        List<ProfileQuestionStrategy> perguntas) {
    this.perguntas = perguntas;
}
```

Assim, adicionar uma nova pergunta não exige criar manualmente uma lista de todas as estratégias.

---

## Facade

A `ClassificationService` funciona como uma porta de entrada única para o sistema.

Quem chama a API não precisa conhecer as dezenas de classes internas.

```java
classificacao = servico.classify(usuario, produto);
```

---

# 11. Fluxo de uma classificação

O fluxo completo pode ser resumido assim:

```text
Aplicativo
    │
    ▼
"Classifique o produto X para o usuário Y"
    │
    ▼
ProfileTagResolver
    │
    ├── questionário
    ├── preferências
    └── alergias
    │
    ▼
ProductSnapshotLoader
    │
    ├── ingredientes
    ├── marca
    ├── embalagem
    ├── selos
    └── regras
    │
    ▼
Cálculo das notas de qualidade
    │
    └── baseScore
    │
    ▼
Cálculo das características do perfil
    │
    └── profileScore
    │
    ▼
AllergyGuard
    │
    └── travas de segurança
    │
    ▼
ExplanationBuilder
    │
    ├── motivos
    └── resumo
    │
    ▼
Gravação da análise
    │
    ▼
Resposta da API
    │
    ├── nota
    ├── faixa
    ├── risco
    ├── motivos
    └── resumo
```

Uma regra importante do desenho é que as classes de cálculo não devem consultar o banco diretamente. O produto e suas regras são carregados antes do cálculo para que as estratégias trabalhem com os dados já disponíveis.

---

# 12. Principais campos do resultado

| Nome | Significado |
|---|---|
| `finalScore` | Nota final de 0 a 100 |
| `baseScore` | Qualidade do produto, de 0 a 35 |
| `profileScore` | Adequação ao perfil, de 0 a 65 |
| `healthScore` | Nota de saúde |
| `environmentalScore` | Nota ambiental |
| `ethicalScore` | Nota ética |
| `performanceScore` | Nota de desempenho |
| `transparencyScore` | Qualidade do cadastro; dado interno |
| `confidenceScore` | Confiança dos dados; dado interno |
| `recommendationLevel` | Faixa da recomendação |
| `riskLevel` | Nível de risco |
| `compatibilityPercentage` | Percentual do perfil atendido |
| `reasons` | Motivos que explicam a nota |
| `summary` | Resumo textual do resultado |

---

# 13. Endpoints principais

### Criar uma nova classificação

```http
POST /api/classifications
```

### Buscar a última análise de uma versão específica

```http
GET /api/classifications/user/{userId}/product-version/{versionId}?scoringModelId=
```

### Buscar análise da versão atual do produto

```http
GET /api/classifications/user/{userId}/product/{productId}?scoringModelId=
```

### Corpo do `POST`

```json
{
  "userId": 42,
  "productVersionId": 118,
  "scoringModelId": 1
}
```

`scoringModelId` é opcional. Quando não informado, é utilizado o modelo atualmente ativo.

---

# 14. Ideia principal para memorizar

```text
                    NOTA FINAL
                   finalScore
                       │
             ┌─────────┴─────────┐
             │                   │
        35 pontos            65 pontos
        baseScore          profileScore
             │                   │
      Qualidade do          Compatibilidade
         produto             com a pessoa
             │                   │
    ┌────────┼────────┐     Perfil + regras
    │        │        │
  Saúde  Ambiental  Ética
           + Desempenho
```

**Em resumo:** a API primeiro avalia o produto em si, depois verifica quanto ele combina com o perfil da pessoa e, por fim, aplica as travas de segurança. O resultado é uma nota de 0 a 100 acompanhada de uma explicação dos motivos.
