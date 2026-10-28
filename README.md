# Venus AI SDK

O núcleo de inteligência da **Venus**, a assistente de skincare e haircare: um
sistema **multiagente** em **LangChain + LangGraph** que responde sobre produtos,
ingredientes e rotina de cuidados, com dados reais do catálogo, memória por usuário
e um juiz que confere cada resposta antes de ela chegar à pessoa.

No app, a pessoa pergunta se um sérum combina com a pele dela, o que faz um
ingrediente, ou pede uma rotina com os produtos que já favoritou. A Venus responde
consultando o catálogo (curado a partir de ANVISA, CosIng e PubChem), as alergias
e os favoritos da própria pessoa, e uma base de perguntas frequentes.

Este repositório é o **motor reutilizável** por trás disso. É uma **biblioteca, não
um serviço**: entrega um grafo LangGraph pronto para ser compilado e invocado por
qualquer backend Python. Hoje quem o consome é a
[Venus-AI-api](https://github.com/Venus-System/Venus-AI-api), que instala o SDK por
tag do Git.

## Sumário

- [O que o SDK faz](#o-que-o-sdk-faz)
- [Os agentes](#os-agentes)
- [O fluxo de uma mensagem](#o-fluxo-de-uma-mensagem)
- [Decisões de projeto](#decisões-de-projeto)
- [Requisitos](#requisitos)
- [Instalação](#instalação)
- [Rodando localmente](#rodando-localmente)
- [Integrando em outro sistema](#integrando-em-outro-sistema)
  - [1. Monte as dependências e compile o grafo](#1-monte-as-dependências-e-compile-o-grafo)
  - [2. Invoque um turno](#2-invoque-um-turno)
  - [3. Leia o resultado](#3-leia-o-resultado)
  - [4. Sessões e memória](#4-sessões-e-memória)
  - [5. Identidade do usuário](#5-identidade-do-usuário)
  - [6. Integrações opcionais](#6-integrações-opcionais)
  - [7. MCP e A2A](#7-mcp-e-a2a)
- [Modelos de linguagem e fallback](#modelos-de-linguagem-e-fallback)
- [Segurança e confiabilidade](#segurança-e-confiabilidade)
- [Configuração](#configuração)
- [Testes e avaliação](#testes-e-avaliação)
- [CI/CD](#cicd)
- [Versões e release](#versões-e-release)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Documentação complementar](#documentação-complementar)
- [Estado do projeto](#estado-do-projeto)
- [Licença](#licença)

## O que o SDK faz

| Capacidade | Como |
| --- | --- |
| Conversa multiagente | Um roteador classifica a mensagem e entrega a um especialista (produto, ingrediente, rotina ou FAQ). |
| Dados reais, não inventados | Os especialistas consultam o Postgres por tools; o juiz confere a resposta contra o retorno bruto dessas tools. |
| Personalização | Alergias, perfil, favoritos e listas do usuário entram na resposta quando o app informa quem é ele. |
| Sessões | Histórico da conversa por `thread_id` (checkpointer do LangGraph, em memória ou MongoDB). |
| Memória de longo prazo | Fatos sobre o usuário que sobrevivem à troca de conversa, por `usuario_id` (store em memória ou MongoDB). |
| RAG | FAQ institucional no Qdrant (ou índice local), com busca na web de reserva e citação das fontes. |
| Segurança | Guardrails de entrada (regex + classificador LLM contra injeção) e de saída (dados sensíveis). |
| Integrações | Google Calendar (agendar a rotina), Neo4j (check-up da rotina), MCP e A2A. |

## Os agentes

| Agente | Papel | Tools |
| --- | --- | --- |
| **Roteador** | Classifica a intenção e escolhe o especialista; responde direto small talk e perguntas fora de escopo. | — |
| **Produto** | Busca produtos, notas, ingredientes e a nota personalizada para o usuário. | `search_product`, `get_product`, `get_product_score`, `get_personalized_score`, `get_product_ingredients`, `get_user_allergies`, `get_user_profile` |
| **Ingrediente** | Explica o que um ingrediente faz, suas propriedades, efeitos e regulação. | `search_ingredient`, `get_ingredient_summary`, `get_ingredient_properties`, `get_ingredient_effects`, `get_ingredient_regulations`, `get_user_allergies`, `get_user_profile`, `converter_concentracao`, `comparar_concentracao_com_limite` |
| **Rotina** | Monta a rotina de manhã e de noite a partir dos favoritos, excluindo o que bate com as alergias. | `get_user_profile`, `get_user_favorites`, `get_user_lists`, `suggest_routine`, `get_user_allergies`, `calcular_tempo_de_uso`, `calcular_datas_de_aplicacao` e, se configurados, as tools do Google Calendar e o `check_routine_health` (Neo4j) |
| **FAQ** | Responde perguntas institucionais (score, privacidade, LGPD, limites da IA) por RAG. | `faq_retriever`, `buscar_na_web` e tools MCP/A2A extras |
| **Juiz** | Confere a resposta do especialista contra o que as tools devolveram; reprova o que não tem lastro. | — |
| **Orquestrador** | Transforma a resposta aprovada no texto final, no tom da Venus. | — |
| **Memória** | Carrega o perfil do usuário no início do turno e grava fatos novos no fim. | — |

As tools de produto, ingrediente e rotina são **consultas estruturadas a um catálogo
já curado**, não RAG. O RAG fica restrito ao FAQ.

## O fluxo de uma mensagem

```
mensagem
   │
   ▼
guardrail de entrada ──bloqueada──────────────────────────────────────┐
   │                                                                  │
   ▼                                                                  │
carregar memória                                                      │
   │                                                                  │
   ▼                                                                  │
roteador ──small talk / fora de escopo────────────────────────────────┤
   │                                                                  │
   ├──▶ produto ─────┐                                                │
   ├──▶ ingrediente ─┤                                                │
   ├──▶ rotina ──────┼──▶ juiz ──aprovado / esgotado──▶ orquestrador ─┤
   └──▶ faq ─────────┘     │                                          │
          ▲                │                                          ▼
          └──reprovado─────┘                                guardrail de saída
       (volta ao MESMO especialista,                                  │
        com o feedback do juiz)                                       ▼
                                                              atualizar memória
                                                                      │
                                                                      ▼
                                                               resposta_final
```

Três detalhes que definem o comportamento:

- **A reprovação não volta ao roteador.** A rota já foi decidida; o juiz só pede
  outra tentativa ao especialista que errou, junto com o motivo. São até
  **2 tentativas** (`MAX_TENTATIVAS_JUIZ`, em `nodes/juiz.py`).
- **Esgotar as tentativas não repassa o texto reprovado.** Nos domínios com dados no
  banco, o orquestrador monta a resposta a partir das evidências das tools, ou
  devolve uma resposta segura genérica.
- **Parte da conferência é feita em código, sem LLM.** Números de tools de cálculo
  que não aparecem na resposta, nota atribuída ao produto errado e alergia afirmada
  sem lastro são reprovados antes de o juiz chamar o modelo.

## Decisões de projeto

Estas escolhas explicam como o SDK deve ser integrado:

1. **O SDK nunca abre conexão sozinho.** Pool do Postgres, checkpointer, store,
   índice do FAQ e tools extras são criados por quem monta o grafo e injetados em
   `compilar_grafo_venus()`. Sem um deles, o grafo ainda compila; só o pedaço que
   depende dele fica indisponível.
2. **Nenhum estado fica preso no processo.** Histórico e memória vivem no
   checkpointer e no store. Com MongoDB, qualquer instância da API atende qualquer
   conversa, o que permite rodar várias réplicas em paralelo.
3. **A identidade vem do estado, nunca do LLM.** As tools com `user_id` usam o
   `usuario_id_postgres` da conversa, mesmo que o modelo informe outro número
   (`tools/_identidade.py`).
4. **A IA não altera dados do usuário.** Não existe tool para adicionar ou remover
   favoritos; pedidos desse tipo recebem uma recusa fixa. Na agenda, o LLM só
   **prepara** a proposta; quem grava o evento é o código, e só depois do "sim" do
   usuário.
5. **Falha de provedor não derruba a conversa.** Todo nó que chama LLM captura a
   exceção e degrada para uma resposta genérica; falha do store faz o turno seguir
   sem memória.

## Requisitos

- **Python 3.10 ou superior** (o CI e a imagem Docker usam 3.12).
- Chave de pelo menos um provedor de LLM: **Groq**, **Mistral** ou **Google Gemini**.
- **PostgreSQL** com o schema `venus` (o `docker-compose.yml` sobe um Postgres 16
  local).
- Opcionais: MongoDB, Qdrant, Neo4j, Tavily e Google Cloud (OAuth do Calendar).

## Instalação

Em outro projeto, instale sempre **por tag**, nunca por branch, para que todo deploy
use exatamente o mesmo código:

```bash
pip install "venus-ai-sdk[mongo,a2a,rag] @ git+https://github.com/Venus-System/Venus-AI-Sdk.git@v0.4.0"
```

Para desenvolver no próprio SDK:

```bash
git clone https://github.com/Venus-System/Venus-AI-Sdk.git
cd Venus-AI-Sdk
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev,a2a,mongo]"
```

O nome de distribuição é `venus-ai-sdk`; o de importação é `venus_sdk`.

### Extras

| Extra | Instala | Para quê |
| --- | --- | --- |
| `mongo` | `langgraph-checkpoint-mongodb`, `pymongo` | Checkpointer e store persistentes no MongoDB. |
| `rag` | `qdrant-client`, `llama-index`, `fastembed` | FAQ no Qdrant com embeddings FastEmbed. |
| `a2a` | `a2a-sdk[http-server]`, `uvicorn` | Expor a Venus como agente A2A. |
| `google_calendar` | `cryptography` | Cifrar o `refresh_token` do Google Calendar. |
| `neo4j` | `neo4j` | Check-up da rotina. |
| `pdf` | `pypdf` | Carregar PDFs no FAQ. |
| `dev` | `pytest`, `pytest-asyncio` e afins | Rodar a suíte de testes. |

## Rodando localmente

```bash
cp .env.example .env              # preencha ao menos uma chave de LLM (GROQ_API_KEY, MISTRAL_API_KEY ou GEMINI_API_KEY)
docker compose up -d              # Postgres 16 em localhost:5432
python scripts/init_db.py         # cria o schema `venus` e carrega um seed fictício
python tests/manual/verificar_tools.py      # chama cada tool de verdade (Postgres, RAG, web, MCP, A2A)
python examples/conversar_com_venus.py      # conversa com a Venus pelo terminal
```

No exemplo do terminal, `VENUS_USER_ID=1` (ou `2`, usuários do seed) liga a
personalização, e `VENUS_DEBUG=1` mostra cada nó, cada chamada de LLM e cada tool,
com o tempo de cada um.

> O plano gratuito do Gemini tem só 20 requisições por dia por modelo. Para uso
> contínuo, prefira Groq ou Mistral como provedor principal.

## Integrando em outro sistema

### 1. Monte as dependências e compile o grafo

Compile **uma vez, no startup** da aplicação, e reaproveite o grafo em todas as
requisições.

```python
import asyncpg

from venus_sdk.config.settings import DATABASE_URL, FAQ_DIR, validar_config
from venus_sdk.flows.venus_flow import compilar_grafo_venus
from venus_sdk.memory import criar_checkpointer_mongo, criar_store_mongo
from venus_sdk.rag import criar_indice_faq

problemas = validar_config(exigir_banco=True)
if problemas:
    raise RuntimeError("; ".join(problemas))

pool = await asyncpg.create_pool(DATABASE_URL)
grafo = compilar_grafo_venus(
    checkpointer=criar_checkpointer_mongo(),   # histórico por thread_id
    store=criar_store_mongo(),                 # memória de longo prazo por usuario_id
    pool=pool,                                 # tools de produto, ingrediente e rotina
    indice_rag=criar_indice_faq(FAQ_DIR),      # Qdrant se houver QDRANT_URL; senão, índice local
)
```

Todos os parâmetros são opcionais. Em desenvolvimento, `criar_checkpointer_em_memoria()`
e `criar_store_em_memoria()` substituem os do Mongo.

### 2. Invoque um turno

O grafo é assíncrono: use `ainvoke` (ou `astream`) **dentro do mesmo event loop**
em que o pool foi criado. Chamar `asyncio.run()` a cada mensagem fecha o loop e
derruba o pool do `asyncpg`.

```python
estado = await grafo.ainvoke(
    {
        "mensagem_usuario": "Esse sérum serve pra pele oleosa?",
        "usuario_id": "6f1c...",          # chave da memória de longo prazo
        "usuario_id_postgres": 42,         # venus.users.user_id, para alergias e personalização
    },
    config={"configurable": {"thread_id": "conversa-123"}},
)
```

Quando o usuário está com um produto aberto na tela, mande a mensagem com o
prefixo `Sobre <produto>: <pergunta>`. O roteador separa o produto da pergunta e
os especialistas tratam "esse produto" como aquele.

### 3. Leia o resultado

| Campo do estado | Significado |
| --- | --- |
| `resposta_final` | O texto para mostrar ao usuário. |
| `expressao` | `"neutra"` ou `"magoada"`: a cara da Venus que acompanha a resposta (a web mostra a Veninha chorando quando alguém a ofende). Use `estado.get("expressao", "neutra")`. |
| `rota` | O especialista escolhido (`produto`, `ingrediente`, `rotina`, `faq`) ou `None` na resposta direta. |
| `aprovado_juiz` / `tentativas_juiz` | O veredito do juiz e quantas vezes ele pediu outra tentativa. |
| `resposta_especialista` | O JSON do especialista, incluindo `fontes_usadas` no FAQ. |
| `entrada_bloqueada` / `saida_bloqueada` | Se algum guardrail trocou a resposta por uma mensagem segura. |

### 4. Sessões e memória

São duas memórias diferentes, e as duas são opcionais:

| | Checkpointer | Store |
| --- | --- | --- |
| Guarda | O histórico bruto de **uma** conversa | Fatos sobre o **usuário** (perfil, preferências) |
| Chave | `thread_id`, em `config["configurable"]` | `usuario_id`, no estado de entrada |
| Sobrevive à troca de conversa? | Não | Sim |
| Sem ela | Passe o histórico em `historico` a cada chamada | Os nós de memória viram no-op |

O agendamento na agenda Google **exige checkpointer**: a proposta espera o "sim" do
usuário no estado da conversa até a mensagem seguinte.

### 5. Identidade do usuário

`usuario_id` (texto, chave da memória) e `usuario_id_postgres` (inteiro, chave do
banco) são campos distintos, e o app deve passar os dois quando os tiver. Sem
`usuario_id_postgres`, as tools que dependem da conta não são chamadas e a Venus
admite que não conseguiu checar alergias ou personalizar, em vez de inventar.

### 6. Integrações opcionais

Entram no agente de Rotina por `tools_rotina_extras`, e no FAQ por
`tools_faq_extras` e `busca_web_faq`:

```python
from venus_sdk.tools.calendario import montar_tools_calendario
from venus_sdk.tools.checkup import montar_tools_checkup

grafo = compilar_grafo_venus(
    ...,
    tools_rotina_extras=[*montar_tools_calendario(pool), *montar_tools_checkup(pool)],
)
```

- **Google Calendar** (extra `google_calendar`): consulta horários livres
  (`check_availability`) e agenda ou remove a rotina em duas etapas, com
  recorrência escolhida pelo usuário. O login no Google é feito pelo backend do
  app; o SDK só guarda o `refresh_token` cifrado com Fernet em
  `venus.google_oauth_tokens`. Escopos: `calendar.freebusy` e `calendar.events`.
  Para testar: `python scripts/conectar_google_calendar.py --user-id 1`.
- **Check-up da rotina** (extra `neo4j`): aponta ativos conflitantes no mesmo
  período, ativos que pedem algo ausente (retinoide sem protetor de manhã), ordem a
  ajustar e produtos repetidos. O Neo4j é uma cópia só de leitura do Postgres mais
  as regras de `venus_sdk/data/checkup/`, sincronizada por
  `python -m venus_sdk.checkup.sincronizar`. Detalhes em [`docs/neo4j.md`](docs/neo4j.md).
- **FAQ no Qdrant** (extra `rag`): com `QDRANT_URL`, a busca usa a coleção
  alimentada por `python -m venus_sdk.rag.faq_ingest`; rode o comando a cada
  mudança nos `.md` de `src/venus_sdk/data/faq/`. Sem Qdrant, o índice é local, em
  memória.

### 7. MCP e A2A

**MCP.** As tools do SDK podem ser expostas como servidor MCP:

```bash
python -m venus_sdk.mcp.servidor                                 # stdio
python -m venus_sdk.mcp.servidor --transport http --porta 8765
```

O servidor MCP não autentica quem chama. Por isso, as 6 tools que recebem `user_id`
só são expostas com `--dados-do-usuario`, e só em rede confiável; sem a flag, são
11 tools. Do outro lado, `venus_sdk.mcp.tools.get_mcp_tools()` carrega tools de
servidores MCP configurados em `MCP_SERVERS` para o agente FAQ.

**A2A.** A Venus pode ser um agente A2A (skills de produto, ingrediente, rotina e
FAQ) ou consultar agentes externos:

```python
from venus_sdk.a2a_server import montar_app_a2a

app = montar_app_a2a(
    grafo=grafo,
    base_url="https://venus.exemplo.com",
    identificar_usuario=minha_funcao_de_autenticacao,   # devolve usuario_id / usuario_id_postgres
)
# uvicorn app:app
```

Sem `identificar_usuario`, as mensagens rodam sem usuário. `identidade_do_metadata`
confia no `metadata` da requisição e só deve ser usada atrás de autenticação. Para
o caminho inverso, `venus_sdk.a2a_client.montar_tool_a2a()` cria a tool
`consultar_agente_externo` a partir de `A2A_AGENTES_EXTERNOS`.

## Modelos de linguagem e fallback

Cada papel usa uma **cadeia de modelos**: se o primeiro falha (cota, rede, erro de
tool call), o próximo assume. Entradas de provedores sem chave no `.env` são
puladas.

| Função | Usada por | Cadeia padrão |
| --- | --- | --- |
| `get_llm_especialista()` | Especialistas | `groq:openai/gpt-oss-120b` → Mistral (`ministral-14b`, `ministral-8b`) → `groq:openai/gpt-oss-20b` → `gemini:gemini-3.6-flash` |
| `get_llm_juiz()` | Juiz | A mesma dos especialistas |
| `get_llm_orquestrador()` | Orquestrador | A mesma, com temperatura 0 |
| `get_llm_rapido()` | Roteador e memória | Mistral (`ministral-14b`, `ministral-8b`) → `groq:openai/gpt-oss-20b` → `groq:openai/gpt-oss-120b` → `gemini:gemini-3.6-flash` |
| `get_llm_guardrail()` | Classificador do guardrail | O 1º elo da cadeia rápida e um de outro provedor |

Com `LLM_PROVIDER=gemini`, a cadeia dos especialistas começa pelo Gemini. Qualquer
cadeia pode ser trocada no formato `provedor:modelo`, separado por vírgula:

```env
LLM_CADEIA_ESPECIALISTA=groq:openai/gpt-oss-120b,gemini:gemini-3.6-flash
LLM_CADEIA_RAPIDO=groq:openai/gpt-oss-20b,groq:llama-3.1-8b-instant
```

## Segurança e confiabilidade

**Guardrail de entrada**, em duas camadas:

1. **Regras determinísticas** (`prompts/guardrail_rules.py`): mensagem vazia ou
   acima de 4.000 caracteres, flood, tentativa de injeção de prompt (inclusive com
   leetspeak e acentuação trocada) e anonimização de dados sensíveis (CPF, RG, CEP,
   cartão).
2. **Classificador LLM**: um modelo rápido classifica como `SEGURO` ou `INJECAO` o
   que a regex deixou passar. Custa uma chamada a mais por mensagem e pode ser
   desligado com `VENUS_GUARDRAIL_LLM=0`. Se os provedores falharem, a mensagem
   passa (fail-open) e o evento é registrado; depois de várias falhas seguidas, um
   disjuntor pausa o classificador para não esperar timeouts.

**Guardrail de saída**: troca por uma mensagem segura a resposta que vazaria dados
sensíveis, e remove emojis (a persona da Venus não usa).

**Juiz**: confere cada resposta de especialista contra o retorno bruto das tools
(`evidencias_tools`). Os prompts reforçam a mesma hierarquia de instruções
(`prompts/comum.py`), como defesa em profundidade.

**Limites de execução**: cada especialista tem teto de passos do agente e de
chamadas de tools por turno, para que uma conversa não fique presa repetindo
consultas.

## Configuração

Copie [`.env.example`](.env.example) para `.env`. **Nunca** faça commit do `.env`.

| Variável | Obrigatória | O que faz |
| --- | --- | --- |
| `GROQ_API_KEY`, `MISTRAL_API_KEY`, `GEMINI_API_KEY` | ao menos uma | Chaves dos provedores de LLM. |
| `LLM_PROVIDER` | não | `groq` (padrão) ou `gemini`: quem lidera a cadeia dos especialistas. |
| `LLM_CADEIA_ESPECIALISTA`, `LLM_CADEIA_RAPIDO` | não | Substituem as cadeias padrão. |
| `DATABASE_URL` | para dados | Postgres das tools de produto, ingrediente e rotina. |
| `MONGODB_URI` | não | Checkpointer e store persistentes (extra `mongo`). |
| `FAQ_DIR` | não | Pasta dos `.md` do FAQ; padrão: o FAQ empacotado no SDK. |
| `QDRANT_URL`, `QDRANT_API_KEY` | não | FAQ no Qdrant (extra `rag`). |
| `TAVILY_API_KEY` | não | Busca na web; sem ela, usa DuckDuckGo. |
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` | não | Check-up da rotina (extra `neo4j`). |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_TOKEN_ENCRYPTION_KEY` | não | Google Calendar (extra `google_calendar`). A chave é Fernet. |
| `MCP_SERVERS` | não | JSON com os servidores MCP que o FAQ consome. |
| `A2A_AGENTES_EXTERNOS` | não | JSON `{"nome": "http://host:porta"}` com agentes A2A externos. |
| `VENUS_GUARDRAIL_LLM` | não | `0` desliga o classificador LLM do guardrail (padrão: ligado). |
| `VENUS_GUARDRAIL_LLM_FALHAS_PARA_ABRIR`, `VENUS_GUARDRAIL_LLM_PAUSA_SEGUNDOS` | não | Disjuntor do classificador: falhas seguidas até pausar (padrão 5) e duração da pausa (padrão 60 s). |
| `FASTEMBED_CACHE_PATH` | não | Pasta do modelo de embeddings, para não baixá-lo a cada start. |
| `VENUS_FASTEMBED_DOWNLOAD` | não | `0` proíbe baixar o modelo; sem cache, cai em embeddings por palavras (`EmbeddingsHash`). |
| `VENUS_FASTEMBED_TIMEOUT_SEGUNDOS` | não | Tempo máximo do download do modelo (padrão 15 s). |
| `VENUS_EMBEDDINGS_LOCAIS` | não | `hash` força o `EmbeddingsHash` no índice local (usado nos testes). |

`venus_sdk.config.settings.validar_config()` devolve a lista do que está faltando,
útil num health check ou no startup.

## Testes e avaliação

```bash
pytest                                     # mais de 500 testes, sem chave de API, sem banco e sem internet
DATABASE_URL=... pytest -m integration     # inclui os testes contra o Postgres real do docker compose
```

A suíte usa um LLM falso com respostas roteirizadas e um pool `asyncpg` falso
(`tests/_fakes.py`). A pasta `tests/regressao/` tem um teste por bug corrigido,
reproduzindo o cenário que falhava. O mapa das pastas está em
[`tests/README.md`](tests/README.md).

Scripts manuais, que usam LLM e banco de verdade e por isso gastam cota:

| Script | O que mede |
| --- | --- |
| `tests/manual/verificar_tools.py` | Chama cada tool contra os serviços reais. |
| `tests/manual/testar_conversas.py` | Conversas reais pelo grafo, só leitura. |
| `tests/manual/avaliar_rag.py` | Hit rate@3, MRR e taxa de "não sei" do RAG ([resultados](docs/avaliacao-rag.md)). |
| `tests/manual/avaliar_guardrail.py` | Regex sozinha × regex + LLM em frases de injeção e legítimas ([resultados](docs/avaliacao-guardrail.md)). |
| `scripts/medir_llms.py` | Latência e disponibilidade dos modelos configurados. |

## CI/CD

| Workflow | Quando roda | O que faz |
| --- | --- | --- |
| `ci.yaml` | PR para `develop` ou `main` | `pytest` em Python 3.12. |
| `deploy-qa.yaml` | Push em `develop` | Testes, build e publicação da imagem Docker no GHCR (staging). |
| `deploy-prod.yaml` | Push em `main` | Testes, build e publicação da imagem no GHCR. O passo de deploy ainda não tem host de produção definido. |
| `avaliar-rag.yaml` | PR que mexe no FAQ ou no RAG, ou manual | Roda a avaliação do RAG. |
| `avaliar-guardrail.yaml` | Manual | Roda a avaliação do guardrail. |

## Versões e release

O SDK segue versionamento semântico, e cada versão tem uma tag no Git e uma seção
no [`CHANGELOG.md`](CHANGELOG.md). Tags publicadas: `v0.1.0`, `v0.2.0`, `v0.3.0` e
`v0.4.0`.

Para lançar uma versão:

1. Na PR que muda o código, suba a versão em `pyproject.toml` (minor para
   funcionalidade nova ou mudança de comportamento, patch só para correção) e
   descreva as mudanças no `CHANGELOG.md`. O teste `tests/pacote/test_versao.py`
   garante que `venus_sdk.__version__` bate com o `pyproject.toml`.
2. Toda PR que muda assinatura pública, variável de ambiente ou comportamento
   padrão precisa de uma entrada em **Mudanças incompatíveis**, com o antes e o
   depois do código do consumidor. Versão sem quebra diz "Nenhuma mudança
   incompatível".
3. Depois do merge na `develop`, com o CI verde, crie a tag no commit do merge:

   ```bash
   git tag -a v0.5.0 -m "v0.5.0: <resumo>" <sha do merge>
   git push origin v0.5.0
   ```

4. Na API, troque o `@v...` em `venus_api/requirements.txt` pela tag nova, rode a
   suíte e abra a PR.

## Estrutura do projeto

```
src/venus_sdk/
├── flows/          # montar_grafo_venus / compilar_grafo_venus e o subgrafo ReAct dos especialistas
├── nodes/          # nós do grafo: guardrails, memória, roteador, especialistas, juiz, orquestrador, agendamento
│   └── state.py    # EstadoVenus, o estado compartilhado entre os nós
├── prompts/        # prompts de cada agente, persona comum e regras de guardrail
├── tools/          # tools LangChain: produto, ingrediente, rotina, cálculos, FAQ, calendário, check-up
├── llm/            # cadeias de modelos com fallback entre Groq, Mistral e Gemini
├── memory/         # checkpointer e store, em memória ou MongoDB
├── rag/            # índice do FAQ (Qdrant ou local), ingestão e busca na web
├── integrations/   # Google Calendar e Neo4j
├── checkup/        # consultas e sincronização do check-up da rotina
├── mcp/            # servidor MCP e cliente das tools MCP
├── a2a_server.py   # a Venus como agente A2A
├── a2a_client.py   # tool para consultar agentes A2A externos
├── config/         # variáveis de ambiente e validar_config()
└── data/           # FAQ e regras do check-up, empacotados no wheel

examples/           # basic_usage.py (topologia) e conversar_com_venus.py (chat no terminal)
scripts/            # init_db.py, schema e seed SQL, Google Calendar, Neo4j, medição de LLMs
tests/              # suíte automática e scripts manuais
docs/               # arquitetura, Neo4j e resultados das avaliações
```

## Documentação complementar

- [`docs/architecture.md`](docs/architecture.md): os módulos do SDK em detalhe.
- [`docs/neo4j.md`](docs/neo4j.md): o check-up da rotina no Neo4j.
- [`docs/avaliacao-rag.md`](docs/avaliacao-rag.md) e
  [`docs/avaliacao-guardrail.md`](docs/avaliacao-guardrail.md): metodologia e
  resultados das avaliações.
- [Arquitetura do sistema inteiro](https://github.com/Venus-System/Venus-AI-api/blob/develop/docs/arquitetura-sistema.md)
  (app, API, bancos, LLMs e integrações), no repositório da API.
- [`CHANGELOG.md`](CHANGELOG.md): o que mudou em cada versão e como migrar.

## Estado do projeto

**Implementado:** sistema multiagente em LangChain/LangGraph, sessões, memória de
longo prazo, RAG com fontes externas, MCP, A2A (servidor e cliente), juiz
anti-alucinação, guardrails, Google Calendar e check-up da rotina.

**Pendente:** observabilidade e SRE: custo estimado para 100 e 1.000 usuários,
latência por agente, taxa de erro, ROI e custo por resolução.

## Licença

[MIT](LICENSE) © 2026 Venus-System.
