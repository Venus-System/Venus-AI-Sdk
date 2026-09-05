# Arquitetura

Visão geral dos módulos do SDK Venus (`src/venus_sdk/`):

- **state.py** — `EstadoVenus`, o `TypedDict` compartilhado entre todos os nós do grafo principal.
- **flows/** — fábricas dos grafos LangGraph:
  - `venus_flow.py` — `montar_grafo_venus(pool=...)`/`compilar_grafo_venus(checkpointer=, store=, pool=)`, o `StateGraph` principal (guardrail de entrada → carregar memória → roteador → especialista → agente juiz → orquestrador → guardrail de saída → atualizar memória). `checkpointer`/`store`/`pool` são sempre injetados por quem monta o grafo — o SDK nunca cria conexão real (Mongo, Postgres) sozinho.
  - `agente_mcp.py` — `montar_agente_mcp()`, o subgrafo ReAct reutilizável pelos especialistas; aceita `tools=` explícito (produto/ingrediente) ou cai no client MCP genérico (`mcp/tools.py`, ainda stub — rotina/FAQ).
- **nodes/** — os nós do grafo principal:
  - `guardrails.py` — `no_guardrail_entrada`, `no_guardrail_saida`.
  - `memoria.py` — `no_carregar_memoria`, `no_atualizar_memoria` (memória de longo prazo, por `usuario_id`).
  - `roteador.py` — `no_roteador`, `decidir_especialista`.
  - `especialistas.py` — `montar_no_agente_produto(pool)`/`montar_no_agente_ingrediente(pool)` (fábricas — o agente só é montado, e as tools só exigem `pool` de verdade, no primeiro uso real do nó) e `no_agente_rotina`/`no_agente_faq` (ainda via o client MCP genérico/stub — Mongo e Qdrant pendentes).
  - `juiz.py` — `no_agente_juiz`, `decidir_pos_juiz`.
  - `orquestrador.py` — `no_orquestrador`.
- **tools/** — tools Postgres (via `asyncpg`, schema `venus`) dos especialistas de produto/ingrediente — consulta estruturada a um catálogo já curado (ETL de ANVISA/CosIng/PubChem), **não é RAG**:
  - `produto.py` — `montar_tools_produto(pool)`: `get_product`, `get_product_score`, `get_personalized_score`, `get_product_ingredients`.
  - `ingrediente.py` — `montar_tools_ingrediente(pool)`: `search_ingredient`, `get_ingredient_summary`, `get_ingredient_properties`, `get_ingredient_effects`, `get_ingredient_regulations`.
  - `compartilhadas.py` — `montar_tools_compartilhadas(pool)`: `get_user_allergies` (usada por produto e ingrediente; rotina também vai usar quando for implementada).
  - Validadas manualmente em 2026-09-05 contra o Postgres de teste real, com dado de verdade (as 9 tools) — sem teste automatizado no CI, mesma razão do checkpointer/store Mongo.
- **prompts/** — os prompts de cada agente (`router.py`, `produto.py`, `ingrediente.py`, `rotina.py`, `faq.py`, `orquestrador.py`, `memoria.py`), com a persona/contexto compartilhados em `comum.py`.
- **mcp/tools.py** — `get_mcp_tools()` e o client MCP genérico, ainda stub — hoje só usado por rotina/FAQ (produto/ingrediente já migraram pra `tools/`, acima). RAG de verdade (fonte externa, requisito da disciplina) é o `faq_retriever` planejado ali, ainda não implementado.
- **guardrail_rules.py** — regras puras de guardrail (`guardrail_entrada`, `guardrail_saida`, `anonimizar_entrada`), sem dependência do grafo/estado.
- **llm/** — clients e wrappers de integração com provedores de LLM (Gemini, Groq).
- **config/** — configurações e variáveis de ambiente do SDK.
- **memory/** — as duas memórias do grafo principal, com a mesma dualidade RAM (dev/teste) / MongoDB (produção, requer extra `mongo` e `MONGODB_URI` no `.env`):
  - `checkpointer.py` — histórico de UMA conversa, por `thread_id`: `criar_checkpointer_em_memoria()` ou `criar_checkpointer_mongo()`.
  - `store.py` — memória de LONGO PRAZO, por `usuario_id` (cross-thread — sobrevive à troca de conversa): `criar_store_em_memoria()` (`InMemoryStore` do LangGraph) ou `criar_store_mongo()` (`MongoDBStore`, implementação própria — o LangGraph não publica um `store` oficial pra Mongo, só `checkpointer`).

Estado atual (pendências, em ordem de prioridade): tools de rotina (MongoDB)
e `faq_retriever` (Qdrant/RAG) ainda são stub em `mcp/tools.py`; client
MCP/A2A de integração com sistemas externos ainda não existe; observabilidade
(custo, latência, taxa de erro, ROI) ainda não existe. Testes em `tests/`
acompanham os módulos de `nodes/`, `tools/` e `memory/`.
