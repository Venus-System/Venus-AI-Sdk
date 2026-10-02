# Testes do Venus SDK

```bash
pytest                    # testes automáticos (sem internet, sem banco, sem LLM)
pytest -m integration     # exige Postgres com schema+seed (scripts/init_db.py)
```

| Pasta | O que testa |
|---|---|
| `nodes/` | Nós do grafo: roteador, especialistas, juiz, orquestrador |
| `flows/` | O grafo inteiro, de ponta a ponta, com LLM e banco falsos |
| `tools/` | Tools do Postgres, RAG/FAQ e Google Calendar |
| `memory/` | Memória da conversa e de longo prazo (store no MongoDB) |
| `guardrails/` | Guardrails de entrada/saída e segurança dos prompts |
| `llm/` | Cadeias de fallback entre provedores de LLM |
| `integracoes/` | MCP e A2A (servidor e cliente) |
| `regressao/` | Um teste por bug corrigido na revisão (cenário que falhava → comportamento certo) |
| `manual/` | Scripts rodados à mão contra serviços reais (o `pytest` não os executa) |
| `fixtures/` | Dados de apoio dos testes |

`_fakes.py` tem os dublês compartilhados (LLM com respostas roteirizadas e pool
`asyncpg` falso); o `conftest.py` deixa importá-lo de qualquer subpasta.

## Testes manuais (`manual/`)

Usam LLMs e banco de verdade — gastam cota e dependem do `.env`:

```bash
python tests/manual/verificar_tools.py    # chama CADA tool (Postgres, RAG, web, MCP, A2A)
python tests/manual/testar_conversas.py   # conversas reais pelo grafo, só leitura
python tests/manual/avaliar_rag.py        # hit rate@3, MRR e "não sei" do RAG do FAQ (--indice qdrant)
```
