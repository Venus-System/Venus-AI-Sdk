"""Tools usadas pelos agentes especialistas.

- Postgres (`asyncpg`): `produto`, `ingrediente`, `compartilhadas`, `rotina` —
  cada `montar_tools_*(pool)` recebe um `asyncpg.Pool` já pronto (criado e
  gerenciado por quem monta o grafo, nunca pelo SDK) e devolve tools `@tool`
  do LangChain, com o pool capturado por closure. Schema `venus`.
- RAG: `faq` — `montar_tools_faq(indice)` (documentos locais + web).
"""
