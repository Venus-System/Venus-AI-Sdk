"""Exemplo mínimo de uso do SDK Venus.

O grafo principal ainda é um esqueleto (ver `venus_sdk/nodes/`): os nós
levantam `NotImplementedError` até que a lógica de cada um seja
implementada. Este exemplo só mostra como montar e inspecionar a topologia
do grafo.
"""

from venus_sdk.flows.venus_flow import montar_grafo_venus

if __name__ == "__main__":
    grafo = montar_grafo_venus()
    print("Nós do grafo Venus:", sorted(grafo.nodes))
