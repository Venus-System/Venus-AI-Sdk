"""Exemplo mínimo de uso do SDK Venus: monta o grafo e inspeciona a topologia.

Para uma conversa de verdade (LLMs + Postgres + RAG), veja
`examples/conversar_com_venus.py`.
"""

from venus_sdk.flows.venus_flow import montar_grafo_venus

if __name__ == "__main__":
    grafo = montar_grafo_venus()
    print("Nós do grafo Venus:", sorted(grafo.nodes))
