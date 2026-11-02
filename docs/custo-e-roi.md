# Custo, latência e ROI

Medição do grafo com LLMs e Postgres de verdade (`tests/manual/medir_custo.py`) e,
a partir dela, o custo estimado para 100 e 1.000 usuários semanais, a latência, o
índice de erros, o custo por resolução e o ROI.

**Base:** 57 turnos em 3 rodadas de 12 conversas (produto, ingrediente, rotina, FAQ,
conversa, fora de escopo e injeção), só leitura, com usuários de teste. A mistura de
assuntos foi escolhida para o teste, então os números são indicativos.

## Como rodar

```bash
python tests/manual/medir_custo.py --saida-json medicao.json   # roda as conversas (LLM e Postgres reais)
python tests/manual/medir_custo.py --ler-json medicao.json     # refaz o resumo de uma medição gravada
```

O FAQ usa o índice local, sem Qdrant; o volume de tokens é o mesmo. Os preços estão
em `PRECOS`, no próprio script.

## Medição: 2026-11-02, branch `feat/faq-sem-web` (`107d092`)

**Ambiente:**
- código: cópia exata do commit, sobre a `v0.5.0`, ainda não lançado;
- produto, ingrediente, rotina e FAQ com texto montado pelo código
  (`VENUS_RESPOSTA_SO_DAS_TOOLS`), cache de prompt ligado e FAQ sem web;
- 3 rodadas seguidas, 57 turnos, com as chaves dos planos gratuitos dos provedores.

| Medida | v0.5.0 | Agora | Meta do plano |
|---|---|---|---|
| Custo por turno | US$ 0,0067 | **US$ 0,00060** (−91%) | −50% |
| Tempo do turno: p95 | 49,8 s | **18,6 s** | abaixo de 20 s |
| Tempo do turno: mediana | 18,6 s | **6,4 s** | — |
| Chamadas de LLM por turno | 12,7 | **4,9** | — |

As duas medições têm as mesmas conversas e o mesmo cálculo de p95. A v0.5.0 foi medida
em `87a44cb`, com os mesmos 57 turnos.

| Rodada | Mediana | p95 | Chamadas de LLM por turno | Custo por turno* |
|---|---|---|---|---|
| 1 | 6,4 s | 18,6 s | 4,9 | US$ 0,00082 |
| 2 | 6,5 s | 17,2 s | 5,2 | US$ 0,00040 |
| 3 | 6,0 s | 15,7 s | 4,6 | US$ 0,00060 |
| **Total** | **6,4 s** | **18,6 s** | **4,9** (3,7 com sucesso) | **US$ 0,00060** |

\* Preço pago de cada provedor (tabela no fim). Nos planos gratuitos, o custo em
dinheiro é zero, com limite de taxa. O custo varia entre rodadas porque depende de
quanto o Mistral reaproveitou do cache (79% da entrada no total).

### Critérios de qualidade do plano

| Critério | Resultado |
|---|---|
| Alergias: conflitos achados no catálogo de produção | 71/71 |
| Alergias: pares sem conflito e sem alerta | 9.199/9.199 |
| Roteamento (54 perguntas, 3 rodadas) | 50, 52 e 50 de 54 (94% no total) |
| Nota e fragrância com o valor certo | 16/16 |
| Respostas sem afirmação sem fonte, segundo o auditor LLM | 29/29 (1 de 30 não auditada) |
| Pergunta que a base não responde (6 armadilhas) | 6/6 sem afirmação sem fonte |

**O que essa mudança não mede:** a qualidade da resposta para quem lê. As respostas ficam
mais secas, sem o tom da Venus, e o FAQ mostra o trecho do documento como está.

### Por rota

| Rota | Turnos | Chamadas de LLM | Tokens (entrada / saída) | Mediana | p95 | Custo por turno |
|---|---|---|---|---|---|---|
| Direto (conversa, fora de escopo) | 9 | 4,3 | 6,4 mil / 98 | 3,5 s | 23,5 s | US$ 0,00040 |
| Produto | 22 | 5,1 | 11,9 mil / 141 | 6,2 s | 18,6 s | US$ 0,00076 |
| Ingrediente | 8 | 4,8 | 10,4 mil / 198 | 12,8 s | 35,0 s | US$ 0,00044 |
| Rotina | 9 | 7,0 | 16,5 mil / 304 | 8,7 s | 12,9 s | US$ 0,00099 |
| FAQ | 6 | 4,5 | 7,0 mil / 51 | 2,4 s | 4,4 s | US$ 0,00027 |
| Bloqueada pelo guardrail | 3 | 0,0 | 0 / 0 | 0,0 s | 0,0 s | US$ 0,00000 |

A mistura de assuntos foi escolhida para o teste. O p95 por rota tem poucos turnos e é
só indicativo.

### Por nó (chamadas que deram certo)

| Nó | Chamadas | Entrada média | Saída média | Tempo médio |
|---|---|---|---|---|
| guardrail_entrada | 54 | 306 | 8 | 0,6 s |
| roteador | 54 | 4.801 | 51 | 1,1 s |
| atualizar_memoria | 54 | 1.295 | 3 | 0,6 s |
| agente_produto | 19 | 6.376 | 119 | 2,1 s |
| agente_rotina | 18 | 5.087 | 113 | 2,1 s |
| agente_ingrediente | 7 | 4.601 | 136 | 3,5 s |
| agente_faq | 6 | 534 | 2 | 0,5 s |

**O que a medição mostra:**
- **Juiz e Orquestrador não chamam LLM nessas rotas:** o Juiz aprovou os 45 turnos de
  especialista por regra em código, e o texto final é montado pelo código.
- **Os especialistas ainda chamam LLM em parte dos turnos:** referência sem produto em
  tela ("qual a nota do primeiro?") e ambiguidade caem no agente ReAct. A rotina também
  usa o agente. Por isso a rotina é a rota mais cara.
- **O turno mais lento** foi "pode usar junto com vitamina C?" (35,0 s), no agente ReAct.
- **A primeira mensagem de duas rodadas** ("oi, tudo bem?") levou 23,5 s e 16,8 s, com só
  4,0 s e 2,0 s em LLM. O resto do tempo ficou fora do LLM, e a causa não foi investigada.

## Custo para 100 e 1.000 usuários semanais

**Premissas:**
- 10 turnos por usuário por semana (cerca de 3 conversas de 3 turnos); mês = 4,33
  semanas;
- a mistura de assuntos da medição;
- planos pagos dos provedores.

| | 100 usuários | 1.000 usuários |
|---|---|---|
| Turnos por mês | 4.330 | 43.300 |
| LLM: medido | US$ 2,62 | US$ 26,17 |
| LLM: pior caso (tudo cai no Gemini 3.6 Flash, sem cache) | US$ 72,58 | US$ 725,76 |
| Infraestrutura | US$ 15,18 | US$ 85,37 |
| **Total (medido)** | **US$ 17,80/mês** | **US$ 111,54/mês** |
| **Por usuário ativo** | **US$ 0,18/mês** | **US$ 0,11/mês** |

Com isso, a infraestrutura passa a ser a maior parte do custo. Na v0.5.0, o total era de
US$ 44,10 e US$ 374,61 por mês.

**Sensibilidade ao uso (LLM, medido):**

| Turnos por usuário por semana | 100 usuários | 1.000 usuários |
|---|---|---|
| 5 | US$ 1,31 | US$ 13,09 |
| 10 | US$ 2,62 | US$ 26,17 |
| 20 | US$ 5,23 | US$ 52,35 |

**Infraestrutura:**
- **100 usuários:** EC2 t3.small (US$ 15,18); MongoDB Atlas M0, Postgres (Aiven) e
  Qdrant Cloud nos planos gratuitos.
- **1.000 usuários:** EC2 t3.medium (US$ 30,37); Atlas Flex no teto (US$ 30); Postgres
  pago estimado em **US$ 25 (premissa: conferir o plano da Aiven)**; Qdrant gratuito
  (o FAQ tem 6 documentos).
- **Fora da conta:** Langfuse e tráfego de rede.

**Plano gratuito:** com 1.000 usuários distribuídos por igual no mês, a média seria de
cerca de 5 chamadas e 10 mil tokens por minuto, abaixo do limite do Ministral 14B
gratuito (30 requisições por minuto). O uso real se concentra em horários de pico, e
esses picos não foram medidos.

## Latência

- **Turno:** mediana de 6,4 s e p95 de 18,6 s; o máximo medido foi 35,0 s.
- **Entre agentes:** tempo médio por chamada na tabela por nó, acima. A latência de
  borda (HTTP) e a de cada nó em produção ficam no Langfuse e no middleware da API.

## Índice de erros

| Medida | Resultado |
|---|---|
| Turnos com erro técnico | 0 de 57 |
| Chamadas de LLM com falha (absorvidas pelo fallback) | 24% (67 de 279): 43 por limite de taxa no `gpt-oss-120b` e 24 por erro HTTP no Ministral 14B |
| Juiz esgotou as tentativas | 0 de 45 turnos de especialista |

Na v0.5.0, o Juiz esgotava as tentativas em 31% desses turnos. A queda vem de ele não
conferir mais um texto de LLM nessas rotas, não de os especialistas terem melhorado.

## Custo por resolução

**Definição de "resolvido":** turno em que a resposta não começa por "Não tenho um dado"
ou "Não encontrei". A definição da v0.5.0 (aprovado pelo Juiz) deixou de separar os casos,
porque o Juiz agora aprova todos. A qualidade das respostas não foi avaliada caso a caso.

- **Resolvidos:** 51 de 57 (89%).
- **Custo por resolução:** US$ 0,0345 ÷ 51 = **US$ 0,00068**, contra US$ 0,00060 por turno.
- **1.000 usuários:** cerca de 38.7 mil resoluções por mês, com os mesmos US$ 26,17 de LLM.

As duas definições são diferentes, então este número não se compara diretamente com o da
v0.5.0 (US$ 0,0089).

## ROI

O modelo de receita do Venus não está definido no projeto, então o cálculo parte do
**ponto de equilíbrio**. As receitas abaixo são premissas para o grupo ajustar.

- **Custo por usuário ativo:** US$ 0,11 a 0,18 por mês.
- **ROI** = (receita por usuário − custo por usuário) ÷ custo por usuário.
- **Exemplo:** com assinatura premium de US$ 2/mês, o equilíbrio exige que 6% a 9% dos
  usuários ativos assinem. Com comissão de afiliado de US$ 1 por compra, seria preciso
  cerca de 11 a 18 compras por 100 usuários por mês.

**Alavancas** (estimativas, não medidas):
- **Roteador:** com cerca de 4,8 mil tokens de entrada por chamada, é o maior prompt fixo
  do turno.
- **Rotina:** é a rota mais cara e ainda passa pelo agente ReAct.
- **Plano pago no Groq:** tende a acabar com as falhas por limite de taxa.

## Preços usados

US$ por milhão de tokens (entrada / saída), consultados em 2026-10-30 em agregadores;
conferir nas páginas oficiais antes de orçar.

| Modelo | Entrada | Saída |
|---|---|---|
| `mistral:ministral-14b-latest` | 0,20 | 0,20 |
| `mistral:ministral-8b-latest` | 0,15 | 0,15 |
| `groq:openai/gpt-oss-120b` | 0,15 | 0,60 |
| `groq:openai/gpt-oss-20b` | 0,075 | 0,30 |
| `gemini:gemini-3.6-flash` | 1,50 | 7,50 |

O Gemini 3.6 Flash está em promoção até 31/12/2026, pela metade desse preço.
Infraestrutura: EC2 on-demand em us-east-1 (t3.small US$ 0,0208/h, t3.medium
US$ 0,0416/h); Atlas Flex com teto de US$ 30/mês.

**Fontes:**
- Groq: [GroqDocs](https://console.groq.com/docs/models),
  [eesel.ai](https://www.eesel.ai/blog/groq-pricing),
  [maximem.ai](https://www.maximem.ai/blog/groq-api-pricing-2026)
- Mistral: [CloudZero](https://www.cloudzero.com/blog/mistral-api-pricing/),
  [pricepertoken](https://pricepertoken.com/pricing-page/provider/mistral-ai)
- Gemini: [eesel.ai](https://www.eesel.ai/blog/gemini-3-6-flash-pricing),
  [morphllm](https://www.morphllm.com/gemini-api-pricing),
  [aipricing.guru](https://www.aipricing.guru/models/gemini-3-6-flash/)
- EC2: [DoiT t3.small](https://www.doit.com/compute/spot/us-east-1/t3.small),
  [DoiT t3.medium](https://www.doit.com/compute/spot/us-east-1/t3.medium)
- MongoDB Atlas: [MongoDB](https://mongodb.com/blog/post/embrace-benefits-serverless-development-atlas?hideMenu=1),
  [oneuptime](https://oneuptime.com/blog/post/2026-03-31-mongodb-atlas-flex-cluster-pricing-model/view)
