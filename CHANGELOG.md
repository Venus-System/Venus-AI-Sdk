# Changelog

Mudanças por versão do SDK. A API instala o SDK por tag (ver "Versões e
release" no README); cada tag tem a sua seção aqui.

## 0.2.0

Correções da revisão técnica e check-up da rotina. Tudo desde `v0.1.0`
(`e91d4f1`).

### Correções
- **FAQ:** falha ao montar o agente (ex.: sem índice) responde `erro_tecnico`
  em vez de derrubar o grafo; a compilação avisa quando o FAQ fica sem índice.
  O índice local usa embeddings semânticos (FastEmbed) e só cai no
  `EmbeddingsHash`, com aviso, quando eles não estão disponíveis (`12a2c91`).
- **Schema:** coluna `firebase_uid TEXT UNIQUE` em `venus.users`, para a API
  resolver o usuário pelo token (`5e7df9e`).
- **Guardrail:** bloqueia injeção em inglês, verbos/objetos em português,
  "DAN" e letras espaçadas; classificador LLM opcional (`057074a`).
- **A2A:** conversas no namespace `a2a:` do checkpointer e `context_id`
  validado (`InvalidParamsError`) (`fffd612`).
- **Check-up:** gravações e consulta de repetidos no Neo4j sem produto
  cartesiano (`81e234c`).

### Funcionalidades
- **Check-up da rotina com Neo4j:** regras de ativos em CSV
  (`data/checkup/`), sincronização (`scripts/sincronizar_neo4j.py`) e tool de
  check-up para o agente de rotina (`44b724e`).
- **Rotina:** horário de cada produto decidido também pela fórmula
  (ingredientes), não só pelo nome (`58264f9`).
- `venus_sdk.__version__`, lida dos metadados do pacote.

### Refatorações e manutenção
- `create_agent` do LangChain no lugar do `create_react_agent` (descontinuado);
  prompt com data atual vira prompt dinâmico. Exige `langchain>=1.0`
  (`f5441ad`).
- Avaliação manual do RAG (`tests/manual/avaliar_rag.py`) e corte de
  relevância documentado (`2e9bcc7`).
- Processo de release por tag e link para os diagramas de arquitetura
  (`7a95a49`, `fb150c5`).

## 0.1.0

Primeira versão com tag (`e91d4f1`): multiagente, memória de longo prazo,
RAG do FAQ (local e Qdrant), MCP, A2A, juiz, guardrails e agendamento da
rotina no Google Calendar.
