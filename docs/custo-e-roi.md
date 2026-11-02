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

## Medição: 2026-11-02, SDK v0.5.0

**Ambiente:**
- código: cópia exata da `develop` em `87a44cb` (tag `v0.5.0`), sem mudanças locais;
- 3 rodadas seguidas, 57 turnos, com as chaves dos planos gratuitos dos provedores;
- cadeia do especialista começando pelo `groq:openai/gpt-oss-120b`, e a rápida pelo
  `mistral:ministral-14b-latest`.

A medição anterior, de 2026-10-30 (19 turnos, US$ 0,0055 por turno, mediana de 21,6 s e
p95 de 58,5 s), foi descartada: ela citava um commit, mas rodou com mudanças locais sem
commit, então não representa nenhuma versão publicada.

| Rodada | Mediana | p95 | Chamadas de LLM por turno | Custo por turno* |
|---|---|---|---|---|
| 1 | 16,9 s | 41,3 s | 12,3 | US$ 0,0065 |
| 2 | 17,1 s | 49,8 s | 12,6 | US$ 0,0066 |
| 3 | 21,9 s | 53,4 s | 13,4 | US$ 0,0069 |
| **Total** | **18,6 s** | **49,8 s** | **12,7** (8,2 com sucesso) | **US$ 0,0067** |

\* Preço pago de cada provedor (tabela no fim). Nos planos gratuitos, o custo em
dinheiro é zero, com limite de taxa.

### Por rota

| Rota | Turnos | Chamadas de LLM | Tokens (entrada / saída) | Mediana | p95 | Custo por turno |
|---|---|---|---|---|---|---|
| Direto (conversa, fora de escopo) | 9 | 3,0 | 6,3 mil / 76 | 4,5 s | 21,0 s | US$ 0,0013 |
| Produto | 22 | 15,8 | 46,3 mil / 907 | 23,5 s | 49,8 s | US$ 0,0093 |
| Ingrediente | 8 | 21,6 | 56,8 mil / 1,4 mil | 42,3 s | 54,4 s | US$ 0,0115 |
| Rotina | 9 | 11,1 | 24,8 mil / 494 | 11,7 s | 22,7 s | US$ 0,0050 |
| FAQ | 6 | 13,2 | 23,2 mil / 763 | 15,4 s | 37,8 s | US$ 0,0047 |
| Bloqueada pelo guardrail | 3 | 0 | 0 | 0 s | 0 s | US$ 0 |

A mistura de assuntos foi escolhida para o teste. O p95 por rota tem poucos turnos e é
só indicativo.

### Por nó (chamadas que deram certo)

| Nó | Chamadas | Entrada média | Saída média | Tempo médio |
|---|---|---|---|---|
| agente_produto | 116 | 6.283 | 129 | 2,8 s |
| agente_ingrediente | 61 | 5.030 | 129 | 2,8 s |
| agente_juiz | 55 | 4.866 | 125 | 2,0 s |
| guardrail_entrada | 54 | 302 | 5 | 0,8 s |
| roteador | 54 | 4.758 | 37 | 1,3 s |
| atualizar_memoria | 54 | 1.263 | 4 | 0,7 s |
| orquestrador | 33 | 3.072 | 113 | 1,8 s |
| agente_rotina | 20 | 5.118 | 104 | 2,2 s |
| agente_faq | 18 | 2.491 | 164 | 3,5 s |

**O que a medição mostra:**
- **98% dos tokens são de entrada.** Os prompts de sistema se repetem a cada passo
  ReAct e a cada nó.
- **As reprovações do Juiz puxam o tempo.** Com 1 tentativa, a mediana é de 12,5 s; com 2,
  de 27,8 s. A segunda tentativa refaz o loop ReAct inteiro do especialista.
- **O modelo principal da cadeia quase nunca atende no plano gratuito.** O `gpt-oss-120b`
  respondeu 43 chamadas e falhou por limite de taxa em 257. O Ministral 14B atendeu 421
  das 465 chamadas que deram certo. Essas falhas custaram 29 dos 1.184 segundos medidos.
- **Guardrail de entrada e memória pesam pouco:** somam cerca de 1,5 s por turno.

## Custo para 100 e 1.000 usuários semanais

**Premissas:**
- 10 turnos por usuário por semana (cerca de 3 conversas de 3 turnos); mês = 4,33
  semanas;
- a mistura de assuntos da medição;
- planos pagos dos provedores.

| | 100 usuários | 1.000 usuários |
|---|---|---|
| Turnos por mês | 4.330 | 43.300 |
| LLM: medido | US$ 28,92 | US$ 289,24 |
| LLM: pior caso (tudo cai no Gemini 3.6 Flash) | US$ 239 | US$ 2.391 |
| Infraestrutura | US$ 15,18 | US$ 85,37 |
| **Total (medido)** | **US$ 44,10/mês** | **US$ 374,61/mês** |
| **Por usuário ativo** | **US$ 0,44/mês** | **US$ 0,37/mês** |

**Sensibilidade ao uso (LLM, medido):**

| Turnos por usuário por semana | 100 usuários | 1.000 usuários |
|---|---|---|
| 5 | US$ 14,46 | US$ 144,62 |
| 10 | US$ 28,92 | US$ 289,24 |
| 20 | US$ 57,85 | US$ 578,49 |

**Infraestrutura:**
- **100 usuários:** EC2 t3.small (US$ 15,18); MongoDB Atlas M0, Postgres (Aiven) e
  Qdrant Cloud nos planos gratuitos.
- **1.000 usuários:** EC2 t3.medium (US$ 30,37); Atlas Flex no teto (US$ 30); Postgres
  pago estimado em **US$ 25 (premissa: conferir o plano da Aiven)**; Qdrant gratuito
  (o FAQ tem 7 documentos).
- **Fora da conta:** Langfuse e tráfego de rede.

**Plano gratuito:** com 1.000 usuários, a média seria de cerca de 25 chamadas e 66 mil
tokens por minuto, perto do limite do Ministral 14B gratuito (30 requisições por minuto).
Os picos passam do limite. Com 100 usuários dá para operar no gratuito, com lentidão nos
picos.

## Latência

- **Turno:** mediana de 18,6 s e p95 de 49,8 s; o máximo medido foi 57,2 s.
- **Entre agentes:** tempo médio por chamada na tabela por nó, acima. A latência de
  borda (HTTP) e a de cada nó em produção ficam no Langfuse e no middleware da API.

## Índice de erros

| Medida | Resultado |
|---|---|
| Turnos com erro técnico | 0 de 57 |
| Chamadas de LLM com falha (absorvidas pelo fallback) | 36% (261 de 726), quase todas por limite de taxa |
| Juiz esgotou as tentativas | 14 de 45 turnos de especialista (31%) |
| Juiz reprovou ao menos uma vez | 25 de 45 turnos de especialista (56%) |

Quando o Juiz esgota as tentativas nos domínios com dado no banco, a resposta é montada
só com o retorno das tools, sem o texto reprovado. Por rota, o esgotamento foi de 7 em 22
(Produto), 5 em 8 (Ingrediente), 2 em 6 (FAQ) e 0 em 9 (Rotina).

Esta medição não separa as reprovações por regra em código das feitas pelo LLM. O script
agora grava `juiz_decidiu_por`, então a próxima medição separa.

## Custo por resolução

**Definição de "resolvido":** turno de especialista aprovado pelo Juiz, ou conversa e
recusa diretas. Ficam de fora os 14 turnos em que o Juiz esgotou as tentativas. A
qualidade das respostas não foi avaliada caso a caso.

- **Resolvidos:** 43 de 57 (75%).
- **Custo por resolução:** US$ 0,38 ÷ 43 = **US$ 0,0089**, contra US$ 0,0067 por turno.
- **1.000 usuários:** cerca de 32,7 mil resoluções por mês, com os mesmos US$ 289 de LLM.

## ROI

O modelo de receita do Venus não está definido no projeto, então o cálculo parte do
**ponto de equilíbrio**. As receitas abaixo são premissas para o grupo ajustar.

- **Custo por usuário ativo:** US$ 0,37 a 0,44 por mês.
- **ROI** = (receita por usuário − custo por usuário) ÷ custo por usuário.
- **Exemplo:** com assinatura premium de US$ 2/mês, o equilíbrio exige que 19% a 22% dos
  usuários ativos assinem. Com comissão de afiliado de US$ 1 por compra, seria preciso
  cerca de 37 a 44 compras por 100 usuários por mês.

**Alavancas** (estimativas, não medidas):
- **Cache de prompt:** como 98% dos tokens são de entrada repetida, é a maior alavanca.
  A Mistral cobra 10% do preço pelo token de entrada em cache.
- **Menos chamadas por turno:** pular o Orquestrador e o Juiz quando a resposta só
  repassa o dado da tool, e pré-filtrar a memória.
- **Plano pago no Groq:** a cadeia roda como foi desenhada e as 36% de falhas por limite de taxa tendem a sumir.

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
