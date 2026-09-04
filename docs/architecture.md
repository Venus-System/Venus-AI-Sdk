# Arquitetura

Visão geral dos módulos do SDK Venus (`src/venus_sdk/`):

- **state.py** — `EstadoVenus`, o `TypedDict` compartilhado entre todos os nós do grafo principal.
- **flows/** — fábricas dos grafos LangGraph:
  - `venus_flow.py` — `montar_grafo_venus()`, o `StateGraph` principal (guardrail de entrada → carregar memória → roteador → especialista → agente juiz → orquestrador → guardrail de saída → atualizar memória).
  - `agente_mcp.py` — `montar_agente_mcp()`, o subgrafo ReAct reutilizável pelos especialistas, com as tools MCP.
- **nodes/** — os nós do grafo principal:
  - `guardrails.py` — `no_guardrail_entrada`, `no_guardrail_saida`.
  - `memoria.py` — `no_carregar_memoria`, `no_atualizar_memoria` (memória de longo prazo, por `usuario_id`).
  - `roteador.py` — `no_roteador`, `decidir_especialista`.
  - `especialistas.py` — `no_agente_produto`, `no_agente_ingrediente`, `no_agente_rotina`, `no_agente_faq`.
  - `juiz.py` — `no_agente_juiz`, `decidir_pos_juiz`.
  - `orquestrador.py` — `no_orquestrador`.
- **prompts/** — os prompts de cada agente (`router.py`, `produto.py`, `ingrediente.py`, `rotina.py`, `faq.py`, `orquestrador.py`, `memoria.py`), com a persona/contexto compartilhados em `comum.py`.
- **mcp/tools.py** — `get_mcp_tools()` e o client MCP consultado pelos especialistas.
- **guardrail_rules.py** — regras puras de guardrail (`guardrail_entrada`, `guardrail_saida`, `anonimizar_entrada`), sem dependência do grafo/estado.
- **llm/** — clients e wrappers de integração com provedores de LLM (Gemini, Groq).
- **config/** — configurações e variáveis de ambiente do SDK.
- **memory/** — as duas memórias do grafo principal, com a mesma dualidade RAM (dev/teste) / MongoDB (produção, requer extra `mongo` e `MONGODB_URI` no `.env`):
  - `checkpointer.py` — histórico de UMA conversa, por `thread_id`: `criar_checkpointer_em_memoria()` ou `criar_checkpointer_mongo()`.
  - `store.py` — memória de LONGO PRAZO, por `usuario_id` (cross-thread — sobrevive à troca de conversa): `criar_store_em_memoria()` (`InMemoryStore` do LangGraph) ou `criar_store_mongo()` (`MongoDBStore`, implementação própria — o LangGraph não publica um `store` oficial pra Mongo, só `checkpointer`).

Os nós em `nodes/` ainda são um esqueleto (levantam `NotImplementedError`);
a topologia do grafo em `flows/venus_flow.py` já reflete o fluxo descrito
nos prompts. Testes em `tests/` (`test_roteador.py`, `test_juiz.py`)
acompanham os módulos de `nodes/`.
