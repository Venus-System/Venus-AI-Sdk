# Arquitetura

Visão geral dos módulos do SDK Venus (`src/venus_sdk/`):

- **state.py** — `EstadoVenus`, o `TypedDict` compartilhado entre todos os nós do grafo principal.
- **flows/** — fábricas dos grafos LangGraph:
  - `venus_flow.py` — `montar_grafo_venus(pool=...)`/`compilar_grafo_venus(checkpointer=, store=, pool=)`, o `StateGraph` principal (guardrail de entrada → carregar memória → roteador → especialista → agente juiz → orquestrador → guardrail de saída → atualizar memória). `checkpointer`/`store`/`pool`/`indice_rag`/`tools_faq_extras` são sempre injetados por quem monta o grafo — o SDK nunca cria conexão real (Mongo, Postgres) sozinho.
  - `agente_mcp.py` — `montar_agente_mcp(llm, prompt=, tools=)`, o subgrafo ReAct reutilizável pelos especialistas; `tools` é obrigatório (Postgres, RAG, MCP e/ou A2A já carregadas).
- **nodes/** — os nós do grafo principal:
  - `guardrails.py` — `no_guardrail_entrada`, `no_guardrail_saida`.
  - `memoria.py` — `no_carregar_memoria`, `no_atualizar_memoria` (memória de longo prazo, por `usuario_id`).
  - `roteador.py` — `no_roteador`, `decidir_especialista`.
  - `especialistas.py` — `montar_no_agente_produto(pool)`/`montar_no_agente_ingrediente(pool)` (fábricas — o agente só é montado, e as tools só exigem `pool` de verdade, no primeiro uso real do nó) e `montar_no_agente_rotina(pool)` (tools Postgres de rotina) e `montar_no_agente_faq(indice, tools_extras)` (RAG: docs locais + web + tools MCP/A2A). O FAQ agora devolve JSON com `fontes_usadas` e passa pelo Agente Juiz.
  - `juiz.py` — `no_agente_juiz`, `decidir_pos_juiz`. Desde 2026-09-10, também recebe `EstadoVenus.evidencias_tools` (retorno bruto de cada tool chamada pelo especialista, ver `nodes/especialistas.py::_extrair_evidencias_tools`) como `RESULTADOS_TOOLS=` na entrada — sem isso, o Juiz só via o JSON final do especialista e aprovava afirmações que a tool citada em `fontes_usadas` nunca sustentou de verdade (achado ao vivo: produto sem ingrediente cadastrado, resposta "inventou" ingredientes, aprovado).
  - `orquestrador.py` — `no_orquestrador`.
- **tools/** — tools Postgres (via `asyncpg`, schema `venus`) dos especialistas de produto/ingrediente — consulta estruturada a um catálogo já curado (ETL de ANVISA/CosIng/PubChem), **não é RAG**:
  - `produto.py` — `montar_tools_produto(pool)`: `search_product`, `get_product`, `get_product_score`, `get_personalized_score`, `get_product_ingredients`. `search_product` foi adicionada em 2026-09-10 — sem ela, uma pergunta que só citava o NOME do produto não tinha como resolver o `product_id`, e o especialista chutava um id (achado ao vivo: alucinou ingredientes de um produto sem nenhum cadastrado).
  - `ingrediente.py` — `montar_tools_ingrediente(pool)`: `search_ingredient`, `get_ingredient_summary`, `get_ingredient_properties`, `get_ingredient_effects`, `get_ingredient_regulations`.
  - `compartilhadas.py` — `montar_tools_compartilhadas(pool)`: `get_user_allergies` (usada por produto e ingrediente; rotina também vai usar quando for implementada).
  - Validadas manualmente em 2026-09-05 contra o Postgres de teste real, com dado de verdade (as 9 tools originais) — sem teste automatizado no CI, mesma razão do checkpointer/store Mongo. `search_product` (2026-09-10) não fez parte dessa validação manual.
  - `get_user_allergies`/`get_personalized_score` exigem um `user_id` inteiro do Postgres — DISTINTO do `usuario_id` string da memória de longo prazo (ver `state.py`). Esse valor chega aos especialistas via `EstadoVenus.usuario_id_postgres` -> `USER_ID_POSTGRES=` no protocolo de entrada (`nodes/especialistas.py::_montar_entrada`) — quem invoca o grafo (o app) precisa fornecê-lo; sem ele, o prompt (`IDENTIFICADOR_USUARIO_NOTA` em `prompts/comum.py`) instrui o especialista a NUNCA inventar um número e a admitir que a checagem de alergia/personalização não pôde ser feita.
- **prompts/** — os prompts de cada agente (`router.py`, `produto.py`, `ingrediente.py`, `rotina.py`, `faq.py`, `orquestrador.py`, `memoria.py`), com a persona/contexto compartilhados em `comum.py`.
- **rag/** — RAG do FAQ: `carregador.py` (chunks de `data/faq`, com fonte/página), `indice.py` (índice vetorial local em numpy, embeddings injetáveis — padrão offline `EmbeddingsHash`, cache `.npz`), `web.py` (Tavily ou DuckDuckGo; nunca levanta). `tools/faq.py`: `faq_retriever` e `buscar_na_web`.
- **tools/rotina.py** — `get_user_profile`, `get_user_favorites`, `get_user_lists`, `add_favorite`, `remove_favorite`, `suggest_routine` (usa só favoritos, exclui produtos que batem com alergias, devolve `sem_produto_para`). `tools/_util.py`: contrato comum — resultado vazio vira `{"encontrado": false, "mensagem": ...}` (nunca `[]`), exceção do driver vira `{"erro": ...}`, busca sem acento sem depender de extensão.
- **mcp/** — `servidor.py` (FastMCP; expõe as tools LangChain, `python -m venus_sdk.mcp.servidor`) e `tools.py` (client via `langchain-mcp-adapters`: `get_mcp_tools()` async, config por `MCP_SERVERS`/`mcp_servers.json`; servidor indisponível → `[]`, não derruba).
- **a2a_server.py / a2a_client.py** — Venus como agente A2A (4 skills; `usuario_id`/`usuario_id_postgres` via `metadata`) e `montar_tool_a2a` (tool `consultar_agente_externo`, agentes em `A2A_AGENTES_EXTERNOS`).
- **guardrail_rules.py** — regras puras de guardrail (`guardrail_entrada`, `guardrail_saida`, `anonimizar_entrada`), sem dependência do grafo/estado.
- **llm/** — clients e wrappers de integração com provedores de LLM (Gemini, Groq).
- **config/** — configurações e variáveis de ambiente do SDK.
- **memory/** — as duas memórias do grafo principal, com a mesma dualidade RAM (dev/teste) / MongoDB (produção, requer extra `mongo` e `MONGODB_URI` no `.env`):
  - `checkpointer.py` — histórico de UMA conversa, por `thread_id`: `criar_checkpointer_em_memoria()` ou `criar_checkpointer_mongo()`.
  - `store.py` — memória de LONGO PRAZO, por `usuario_id` (cross-thread — sobrevive à troca de conversa): `criar_store_em_memoria()` (`InMemoryStore` do LangGraph) ou `criar_store_mongo()` (`MongoDBStore`, implementação própria — o LangGraph não publica um `store` oficial pra Mongo, só `checkpointer`).

Estado atual: tools de produto/ingrediente/rotina (Postgres, schema+seed em
`scripts/sql`), RAG (local + web), MCP e A2A (servidor e client) implementados e
verificados por `scripts/verificar_tools.py` e pelos testes em `tests/`
(`pytest -m integration` roda as tools contra um Postgres real). Pendência:
observabilidade (custo, latência, taxa de erro, ROI). O roteador tem uma rede de
segurança (`nodes/roteador.py::e_small_talk`) contra saudação roteada para
especialista.

Resiliência a falha de LLM (2026-09-10): rodando o grafo de verdade contra
os LLMs reais, uma cota do Gemini estourada no meio de uma conversa chegou a
subir como exceção crua até o `.ainvoke()` do grafo principal (produto/
ingrediente/rotina/faq via `nodes/especialistas.py`, e o orquestrador/
`nodes/orquestrador.py` — o roteador e a memória de longo prazo já eram
resilientes a isso). Agora todo nó que chama um LLM (especialistas,
orquestrador, Agente Juiz) captura qualquer exceção do provedor e degrada
pra uma resposta genérica em vez de derrubar a conversa inteira — o
Agente Juiz reprova essa resposta genérica normalmente, e depois de
`MAX_TENTATIVAS_JUIZ` o orquestrador ainda comunica o problema com
transparência (mesmo caminho já usado pro caso "esgotado"). Isso não
depende de ter identificado a causa exata de o fallback
`get_llm_gemini().with_fallbacks([get_llm_groq()])` não ter coberto aquela
chamada específica (pode ter sido os dois provedores falhando ao mesmo
tempo) — o objetivo aqui é só garantir que o grafo nunca quebra por causa
de um provedor de LLM indisponível.
