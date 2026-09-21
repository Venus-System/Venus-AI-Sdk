# Venus AI SDK

SDK do assistente **Venus** (skincare/haircare): sistema **multiagente** em
**LangChain + LangGraph**, com sessões por usuário, memória de longo prazo,
RAG com fontes externas, MCP, A2A, juiz anti-alucinação e guardrails.

## Arquitetura (resumo)

```
guardrail entrada → carregar memória → roteador ─┬→ produto ────┐
                                                 ├→ ingrediente ├→ juiz ─→ orquestrador → guardrail saída → atualizar memória
                                                 ├→ rotina ─────┤    ↑ reprova: volta ao especialista (feedback)
                                                 ├→ faq (RAG) ──┘
                                                 └→ resposta direta (small talk / fora de escopo)
```

| Agente | Tools |
|---|---|
| Roteador | — (LLM rápido + rede de segurança contra small talk mal roteado) |
| Produto | `search_product`, `get_product`, `get_product_score`, `get_personalized_score`, `get_product_ingredients`, `get_user_allergies` (Postgres) |
| Ingrediente | `search_ingredient`, `get_ingredient_summary/properties/effects/regulations`, `get_user_allergies` (Postgres) |
| Rotina | `get_user_profile`, `get_user_favorites`, `get_user_lists`, `add_favorite`, `remove_favorite`, `suggest_routine`, `get_user_allergies` (Postgres) |
| FAQ (RAG) | `faq_retriever` (docs locais), `buscar_na_web` (Tavily/DuckDuckGo), tools MCP e A2A opcionais |
| Juiz | — confere resposta × retorno bruto das tools (`evidencias_tools`) |
| Orquestrador / Memória | — |

- **Sessões**: `thread_id` (checkpointer, histórico da conversa) + `usuario_id` (memória de longo prazo, `memory/store.py`).
- **RAG**: `data/faq/*.md|txt|pdf` → índice local (`rag/`); a resposta cita as fontes em `fontes_usadas`.
- **MCP**: `python -m venus_sdk.mcp.servidor` expõe as 19 tools; `mcp/tools.py` as consome (`get_mcp_tools`).
- **A2A**: `a2a_server.py` (Venus como agente A2A, skills produto/ingrediente/rotina/faq; identidade via `metadata`) e `a2a_client.py` (Venus consulta agente externo).
- Detalhes em [`docs/architecture.md`](docs/architecture.md).

## Como rodar

```bash
pip install -e ".[dev,a2a,mongo]"          # mongo é opcional
cp .env.example .env                        # preencha GEMINI_API_KEY e GROQ_API_KEY
# LLM_PROVIDER=groq (padrão) ou gemini: quem responde primeiro; o outro é fallback.
# O plano GRATUITO do Gemini tem só 20 requisições/dia por modelo (erro 429 RESOURCE_EXHAUSTED).

docker compose up -d                        # Postgres 16
python scripts/init_db.py                   # schema `venus` + seed fictício
python scripts/verificar_tools.py           # chama CADA tool (Postgres, RAG, web, MCP, A2A)

python examples/conversar_com_venus.py      # conversa no terminal (VENUS_USER_ID=1 para personalização)
```

### Testes

```bash
pytest                                       # 230+ testes, sem chaves de API e sem banco
DATABASE_URL=... pytest -m integration       # + integração contra o Postgres real do docker compose
```

### Servidores

```bash
python -m venus_sdk.mcp.servidor                          # MCP via stdio
python -m venus_sdk.mcp.servidor --transport http --porta 8765
```

```python
# A2A: o app Starlette é montado com o grafo já compilado
from venus_sdk.a2a_server import montar_app_a2a
app = montar_app_a2a(grafo=grafo, base_url="http://localhost:9000")   # uvicorn app:app
# identidade por request: metadata={"usuario_id": "u1", "usuario_id_postgres": 1}
```

## Estado do projeto

Implementado: multiagente, LangChain/LangGraph, sessões, memória de longo prazo, RAG (local + web),
MCP, A2A (servidor + client), juiz, guardrails. **Pendente**: observabilidade/SRE (custo para 100/1000
usuários, latência por agente, taxa de erro, ROI, custo por resolução).
