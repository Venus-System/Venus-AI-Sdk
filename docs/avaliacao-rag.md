# Avaliação do RAG do FAQ

Resultado de `tests/manual/avaliar_rag.py` contra os documentos do FAQ que vão
para produção (`venus_sdk/data/faq`, empacotados no SDK). Refaça a avaliação
quando os documentos, a divisão em trechos ou o modelo mudarem.

## Como rodar

```bash
python tests/manual/avaliar_rag.py                         # índice local + FastEmbed
python tests/manual/avaliar_rag.py --embeddings hash       # índice local + EmbeddingsHash
python tests/manual/avaliar_rag.py --indice qdrant         # Qdrant em memória + FastEmbed
python tests/manual/avaliar_rag.py --detalhes              # + top 3 de cada pergunta no corte de produção
python tests/manual/avaliar_rag.py --faq-dir outra/pasta   # outros documentos
```

**Métricas** (`k = 3`):
- **hit@3:** alguma fonte esperada entre os 3 trechos devolvidos;
- **MRR:** 1/posição do primeiro trecho certo;
- **"não sei" correto:** nas perguntas sem resposta no FAQ, nenhum trecho
  acima do corte.

**Conjunto:** `tests/fixtures/avaliacao_rag.jsonl`, com 31 perguntas: 24 com
resposta no FAQ e 7 sem. Inclui as 3 da revisão técnica 2:
- "vocês vendem meus dados?", com fontes `privacidade_e_dados.md` e
  `dados_e_privacidade_lgpd.md`;
- "posso confiar na IA se tenho alergia?", com fontes `alergias_e_limites.md`
  e `limites_da_ia.md`;
- "quem criou o aplicativo?", sem resposta: o FAQ não diz quem criou o app,
  então o certo é "não sei".

## Configurações avaliadas

| | Índice local + FastEmbed | Índice local + hash | Qdrant + FastEmbed |
|---|---|---|---|
| Quando a API usa | sem `QDRANT_URL`, com o extra `rag` e o modelo baixado | sem `QDRANT_URL` e sem o modelo (fallback, com aviso no log) | com `QDRANT_URL` |
| Divisão em trechos | `rag/carregador.py` | `rag/carregador.py` | `faq_ingest.py` (`MarkdownNodeParser` + `SentenceSplitter` 400/50) |
| Corte de produção | 0,3 (`rag/faq.py::_SCORE_MINIMO`) | 0,1 (`rag/indice.py::SCORE_MINIMO_HASH`) | 0,3 |

**Ambiente:**
- data: 2026-10-02;
- código: branch `fix/revisao-tecnica-2` do SDK, sobre a develop `7523ee4`,
  no commit que adicionou este documento;
- Python 3.11.2;
- fastembed 0.8.1, com o modelo
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`;
- llama-index-core 0.14.25;
- qdrant-client 1.19.1, com o Qdrant em memória;
- langchain-text-splitters 1.1.2.

## Antes: trechos de 700 caracteres sem olhar as seções

A divisão antiga do `rag/carregador.py` usava só `RecursiveCharacterTextSplitter`
com 700 caracteres. O `api_de_classificacao.md`, com 739 linhas de tabelas e
código, virava dezenas de pedaços sem contexto que ganhavam de documentos de
outros assuntos.

| Corte | Local + FastEmbed: hit@3 | MRR | "não sei" | Local + hash: hit@3 | MRR | "não sei" |
|---|---|---|---|---|---|---|
| 0,05 | | | | 92% | 0,70 | 0% |
| 0,10 | | | | **92%** | **0,70** | **0%** |
| 0,15 | | | | 83% | 0,67 | 57% |
| 0,20 | 79% | 0,63 | 57% | 71% | 0,60 | 86% |
| 0,25 | 79% | 0,63 | 71% | 50% | 0,46 | 86% |
| 0,30 | **79%** | **0,63** | **71%** | 29% | 0,27 | 100% |
| 0,35 | 75% | 0,61 | 71% | | | |
| 0,40 | 62% | 0,51 | 100% | | | |

O índice local com FastEmbed ficou **abaixo da meta de hit@3 de 0,8**. Erros
no corte de produção:
- "Como faço para apagar minha conta?" não trouxe nada acima de 0,3;
- "Como edito as informações do meu perfil?" trouxe 3 trechos do
  `api_de_classificacao.md`;
- "Se eu digitar meu CPF no chat, ele fica salvo?" não trouxe o
  `privacidade_e_dados.md`;
- "quem criou o aplicativo?" trouxe um trecho do `sobre_o_venus.md`;
- "Qual a melhor linguagem de programação para iniciantes?" trouxe trechos em
  vez de "não sei".

## Depois: divisão por seção do markdown

Desde esta versão, `rag/carregador.py` divide os `.md` por seção (`#`, `##`,
`###`) antes de dividir por tamanho, e cada trecho começa com o caminho de
títulos. Por exemplo: `API de Classificação — Venus > 3. baseScore > 3.3
ethicalScore > Exemplo`. Um trecho nunca mistura duas seções, e o embedding
sabe de onde ele veio. A mudança vale para o índice local; o Qdrant já
dividia por seção na ingestão.

| Corte | Local + FastEmbed: hit@3 | MRR | "não sei" | Local + hash: hit@3 | MRR | "não sei" |
|---|---|---|---|---|---|---|
| 0,05 | | | | 92% | 0,78 | 0% |
| 0,10 | | | | **92%** | **0,78** | **0%** |
| 0,15 | | | | 92% | 0,78 | 57% |
| 0,20 | 92% | 0,76 | 43% | 79% | 0,69 | 71% |
| 0,25 | 88% | 0,74 | 57% | 62% | 0,58 | 86% |
| 0,30 | **88%** | **0,74** | **86%** | 46% | 0,46 | 100% |
| 0,35 | 75% | 0,66 | 86% | | | |
| 0,40 | 75% | 0,66 | 100% | | | |

**Qdrant + FastEmbed** (a divisão não muda, porque é a da ingestão):

| Corte | hit@3 | MRR | "não sei" |
|---|---|---|---|
| 0,20 | 88% | 0,79 | 71% |
| 0,25 | 88% | 0,79 | 71% |
| 0,30 | **83%** | **0,77** | **71%** |
| 0,35 | 58% | 0,56 | 71% |
| 0,40 | 54% | 0,54 | 100% |

Em negrito, o corte de produção de cada configuração.

## Conclusões

- **Local + FastEmbed** passa a meta: hit@3 de 0,79 para 0,88 e "não sei"
  correto de 71% para 86% no corte 0,3. O corte de 0,3 continua o melhor
  equilíbrio. Em 0,4, o "não sei" chega a 100%, mas o hit@3 cai para 75%.
- **Qdrant + FastEmbed** também passa a meta: hit@3 de 0,83 no corte 0,3.
- **Local + hash** acerta a fonte (hit@3 de 0,92 no corte 0,1), mas **nunca
  diz "não sei"**: perguntas fora do assunto ("capital da França") sempre
  trazem algum trecho acima de 0,1. Como é busca por palavras, não
  semântica, ele serve só de fallback. Por isso a API loga `error` quando
  cai nele em produção, e a imagem Docker já leva o modelo do FastEmbed.
- **Ainda fraco:** "vocês vendem meus dados?" conta como acerto, porque o
  `dados_e_privacidade_lgpd.md` está no top 3. Mas, nas duas configurações
  com FastEmbed, o primeiro trecho é uma tabela do `api_de_classificacao.md`,
  e o `privacidade_e_dados.md` (o que responde direto: "não vende
  informações de usuários") só aparece no Qdrant. Uma seção com título em
  forma de pergunta ("O Venus vende meus dados?") no `privacidade_e_dados.md`
  deve resolver. É mudança de conteúdo, então fica para quem cuida do FAQ.
- **Sempre erra:** "quem criou o aplicativo?" traz um trecho do
  `sobre_o_venus.md` com score em torno de 0,36. Nesse caso o agente FAQ
  precisa dizer que o trecho não responde: o prompt manda não inventar.

## Rodada 3: seções em forma de pergunta

Revisão técnica 3, em 2026-10-04, com o mesmo ambiente e as mesmas
configurações da rodada anterior. O código é o da branch
`fix/revisao-tecnica-3`, sobre a `v0.2.0` (`4518636`), no commit que mudou
os documentos.

**O que mudou nos documentos.** Só reorganizei informação que já estava no
FAQ; nada de conteúdo novo:
- `privacidade_e_dados.md` ganhou duas seções:
  - `## O Venus vende meus dados?`
  - `## O que acontece se eu digitar meu CPF no chat?`
- `como_funciona_o_score.md` ganhou a seção
  `## O que acontece com a nota quando a fórmula de um produto muda?`.
- `alergias_e_limites.md` foi dividido em seções com título em forma de
  pergunta:
  - "Como cadastro minhas alergias?"
  - "E se o Venus não souber quem eu sou?"
  - "O que o Venus faz e o que ele não faz?"
  - "De onde vêm as restrições regulatórias?"
- `sobre_o_venus.md`:
  - "O que é o app e o que ele faz" virou "O que é o Venus (Vênus) e o que
    ele faz?";
  - "Significado dos selos" virou "O que significam os selos (cruelty-free,
    vegano, marca brasileira)?".
- "quem criou o aplicativo?" continua **sem resposta**. Nenhuma fonte do
  projeto (README, `pyproject.toml`, LICENSE) diz quem criou o app, então
  ficou um `TODO(produto)` num comentário HTML no `sobre_o_venus.md`.
  Comentários HTML não vão para o índice: desde esta rodada, o índice local e
  a ingestão no Qdrant descartam esses comentários.

| Corte | Local + FastEmbed: hit@3 | MRR | "não sei" | Local + hash: hit@3 | MRR | "não sei" |
|---|---|---|---|---|---|---|
| 0,05 | | | | 92% | 0,83 | 0% |
| 0,10 | | | | **92%** | **0,83** | **0%** |
| 0,15 | | | | 92% | 0,83 | 57% |
| 0,20 | 96% | 0,87 | 43% | 88% | 0,81 | 57% |
| 0,25 | 96% | 0,87 | 71% | 62% | 0,62 | 71% |
| 0,30 | **96%** | **0,87** | **100%** | 54% | 0,54 | 100% |
| 0,35 | 96% | 0,87 | 100% | | | |
| 0,40 | 96% | 0,87 | 100% | | | |

**Qdrant + FastEmbed:**

| Corte | hit@3 | MRR | "não sei" |
|---|---|---|---|
| 0,20 | 92% | 0,81 | 71% |
| 0,25 | 92% | 0,81 | 71% |
| 0,30 | **88%** | **0,79** | **71%** |
| 0,35 | 71% | 0,62 | 86% |
| 0,40 | 62% | 0,56 | 100% |

**Comparação no corte de produção:**

| Configuração | Rodada 2: hit@3 | MRR | "não sei" | Rodada 3: hit@3 | MRR | "não sei" |
|---|---|---|---|---|---|---|
| Local + FastEmbed (0,3) | 88% | 0,74 | 86% | **96%** | **0,87** | **100%** |
| Qdrant + FastEmbed (0,3) | 83% | 0,77 | 71% | **88%** | **0,79** | 71% |
| Local + hash (0,1) | 92% | 0,78 | 0% | 92% | **0,83** | 0% |

**Casos da revisão:**
- **"vocês vendem meus dados?"**: o `privacidade_e_dados.md` agora vem **em
  primeiro lugar** nas duas configurações com FastEmbed: 0,489 no índice
  local e 0,425 no Qdrant. Antes, o primeiro trecho era uma tabela do
  `api_de_classificacao.md`.
- **"quem criou o aplicativo?"**: o índice local agora não traz nada, que é o
  "não sei" correto; antes trazia um trecho do `sobre_o_venus.md` com score
  0,36. No Qdrant ainda vem um trecho do `api_de_classificacao.md` com score
  0,306, logo acima do corte.

**O que continua fraco:**
- "O que significam os selos?": o primeiro trecho ainda é do
  `api_de_classificacao.md`. Testei o título "O que significam os selos do
  app?". Ele resolvia os selos (hit@3 local de 100%, MRR 0,91), mas fazia
  "quem criou o aplicativo?" puxar o `sobre_o_venus.md` com score 0,42, e o
  "não sei" local caía para 86%. Fiquei com a versão que mantém o "não sei"
  em 100%: nesse caso, errar a fonte de uma pergunta é menos grave que
  responder algo que o FAQ não diz.
- No Qdrant, "Como meus dados são usados para gerar as recomendações?" e
  "posso confiar na IA se tenho alergia?" trazem fontes que não estão entre as
  esperadas.

**Antes de valer em produção:** com `QDRANT_URL` configurada, é preciso
**rodar a ingestão de novo** (`python -m venus_sdk.rag.faq_ingest`), senão a
coleção continua com os documentos antigos.
