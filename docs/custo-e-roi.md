# Custo, latência e ROI

Medição do grafo com LLMs e Postgres de verdade (`tests/manual/medir_custo.py`) e,
a partir dela, o custo estimado para 100 e 1.000 usuários semanais, a latência, o
índice de erros, o custo por resolução e o ROI.

**Base:** 19 turnos em 12 conversas (produto, ingrediente, rotina, FAQ, conversa, fora
de escopo e injeção), só leitura, com os usuários de teste do seed. É uma rodada só,
com amostra pequena e a mistura de assuntos escolhida para o teste: os números são
indicativos.

## Como rodar

```bash
python tests/manual/medir_custo.py --saida-json medicao.json   # roda as conversas (LLM e Postgres reais)
python tests/manual/medir_custo.py --ler-json medicao.json     # refaz o resumo de uma medição gravada
```

O FAQ usa o índice local, sem Qdrant; o volume de tokens é o mesmo. Os preços estão
em `PRECOS`, no próprio script.

## Medição: 2026-10-30

**Ambiente:**
- código: branch `fix/rotina-sugestao-alergia`, sobre `4a0c26c`;
- planos gratuitos dos provedores; cadeia do especialista começando pelo
  `groq:openai/gpt-oss-120b`, e a rápida pelo `mistral:ministral-14b-latest`.

### O que acontece em um turno

| Rota | Turnos | Chamadas de LLM | Tokens (entrada / saída) | Tempo (mediana) | Custo por turno* |
|---|---|---|---|---|---|
| Direto (conversa, fora de escopo) | 5 | 3 | 6,3 mil / 110 | 5s | US$ 0,0013 |
| Produto | 6 | 16 | 45 mil / 890 | 38s | US$ 0,0092 |
| Ingrediente | 3 | 16 | 38 mil / 1,1 mil | 58s | US$ 0,0078 |
| Rotina | 2 | 11 | 23 mil / 370 | 15s | US$ 0,0047 |
| FAQ | 2 | 13,5 | 23,5 mil / 870 | 29s | US$ 0,0048 |
| Bloqueada pelo guardrail | 1 | 0 | 0 | 0s | US$ 0 |
| **Média** | **19** | **11** | **26,8 mil / 615** | **21,6s (p95 58,5s)** | **US$ 0,0055** |

\* Preço pago de cada provedor (tabela no fim). Nos planos gratuitos, o custo em
dinheiro é zero, com limite de taxa.

**Por nó (chamadas que deram certo):**

| Nó | Chamadas | Entrada média | Saída média | Tempo médio |
|---|---|---|---|---|
| agente_produto | 29 | 6.500 | 143 | 5,0s |
| guardrail_entrada | 18 | 301 | 5 | 1,0s |
| roteador | 18 | 4.703 | 58 | 2,2s |
| atualizar_memoria | 18 | 1.281 | 9 | 1,1s |
| agente_juiz | 15 | 4.726 | 118 | 3,4s |
| agente_ingrediente | 15 | 4.780 | 142 | 4,1s |
| orquestrador | 9 | 3.291 | 103 | 4,1s |
| agente_faq | 6 | 2.534 | 174 | 4,7s |
| agente_rotina | 4 | 5.009 | 96 | 2,6s |

**O que a medição mostra:**
- **98% dos tokens são de entrada.** Os prompts de sistema se repetem a cada passo
  ReAct e a cada nó.
- **O modelo principal da cadeia nunca rodou.** As 77 chamadas que falharam (37% de
  209) foram todas `RateLimitError` do `gpt-oss-120b` no Groq: o plano gratuito permite
  8 mil tokens por minuto, e um prompt de especialista sozinho passa de 6 mil. Das 132
  chamadas que deram certo, 131 rodaram no Ministral 14B, o elo seguinte.
- **A lentidão vem da quantidade de chamadas.** As falhas custaram 24 dos 432 segundos.
  O que pesa são cerca de 16 chamadas em sequência numa pergunta de produto, de 2 a 5
  segundos cada.

## Custo para 100 e 1.000 usuários semanais

**Premissas:**
- 10 turnos por usuário por semana (cerca de 3 conversas de 3 turnos); mês = 4,33
  semanas;
- a mistura de assuntos da medição;
- planos pagos dos provedores.

| | 100 usuários | 1.000 usuários |
|---|---|---|
| Turnos por mês | 4.330 | 43.300 |
| LLM: medido (Ministral 14B) | US$ 23,73 | US$ 237,28 |
| LLM: cadeia como desenhada (gpt-oss-120b nos especialistas) | US$ 20,18 | US$ 201,78 |
| LLM: pior caso (tudo cai no Gemini 3.6 Flash) | US$ 194,03 | US$ 1.940,27 |
| Infraestrutura | US$ 15,18 | US$ 85,37 |
| **Total (medido)** | **US$ 38,91/mês** | **US$ 322,65/mês** |
| **Por usuário ativo** | **US$ 0,39/mês** | **US$ 0,32/mês** |

**Sensibilidade ao uso (LLM, medido):**

| Turnos por usuário por semana | 100 usuários | 1.000 usuários |
|---|---|---|
| 5 | US$ 11,86 | US$ 118,64 |
| 10 | US$ 23,73 | US$ 237,28 |
| 20 | US$ 47,46 | US$ 474,57 |

**Infraestrutura:**
- **100 usuários:** EC2 t3.small (US$ 15,18); MongoDB Atlas M0, Postgres (Aiven) e
  Qdrant Cloud nos planos gratuitos.
- **1.000 usuários:** EC2 t3.medium (US$ 30,37); Atlas Flex no teto (US$ 30); Postgres
  pago estimado em **US$ 25 (premissa: conferir o plano da Aiven)**; Qdrant gratuito
  (o FAQ tem 7 documentos).
- **Fora da conta:** Langfuse e tráfego de rede.

**Plano gratuito:** com 1.000 usuários, a média seria de 22 chamadas e 54 mil tokens
por minuto, perto do limite do Ministral 14B gratuito (30 requisições por minuto). Um
turno de produto dispara 16 chamadas em cerca de 40 segundos, então os picos passam do
limite. Com 100 usuários dá para operar no gratuito, com lentidão nos picos.

## Latência

- **Turno:** mediana de 21,6s e p95 de 58,5s; produto e ingrediente entre 38s e 58s.
- **Entre agentes:** tempo médio por chamada na tabela por nó, acima. A latência de
  borda (HTTP) e a de cada nó em produção ficam no Langfuse e no middleware da API.

## Índice de erros

| Medida | Resultado |
|---|---|
| Turnos com erro técnico | 0 de 19 |
| Chamadas de LLM com falha (absorvidas pelo fallback) | 37% (77 de 209), todas por limite de taxa |
| Juiz esgotou as tentativas | 5 de 13 turnos de especialista (38%) |
| Roteador respondeu sozinho a pergunta de seguimento | 2 de 19 |

Quando o Juiz esgota as tentativas, a resposta é montada só com o retorno das tools, sem
o texto reprovado. As 2 respostas diretas ("e de manhã?" e "quais os ingredientes
dele?") não passaram por especialista nem pelo Juiz.

## Custo por resolução

**Definição conservadora de "resolvido":** turno de especialista aprovado pelo Juiz, ou
conversa e recusa corretas. Ficam de fora os 5 turnos em que o Juiz esgotou as
tentativas e as 2 respostas diretas não verificadas.

- **Resolvidos:** 12 de 19 (63%).
- **Custo por resolução:** US$ 0,104 ÷ 12 = **US$ 0,0087**, contra US$ 0,0055 por turno.
- **1.000 usuários:** cerca de 27 mil resoluções por mês, com os mesmos US$ 237 de LLM.

## ROI

O modelo de receita do Venus não está definido no projeto, então o cálculo parte do
**ponto de equilíbrio**. As receitas abaixo são premissas para o grupo ajustar.

- **Custo por usuário ativo:** US$ 0,32 a 0,39 por mês.
- **ROI** = (receita por usuário − custo por usuário) ÷ custo por usuário.
- **Exemplo:** com assinatura premium de US$ 2/mês, o equilíbrio exige que 16% a 19% dos
  usuários ativos assinem. Com comissão de afiliado de US$ 1 por compra, seria preciso
  cerca de 1 compra a cada 3 usuários por mês.

**Alavancas** (estimativas, não medidas):
- **Cache de prompt:** como 98% dos tokens são de entrada repetida, é a maior alavanca.
  A Mistral cobra 10% do preço pelo token de entrada em cache.
- **Menos chamadas por turno:** pular o Orquestrador e o Juiz quando a resposta só
  repassa o dado da tool, e pré-filtrar a memória.
- **Plano pago no Groq:** a cadeia roda como foi desenhada e as 37% de falhas somem.

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
