"""Client MCP e as tools expostas aos agentes especialistas.

TODO: escolher e adicionar a dependência do client MCP (ex.:
`langchain-mcp-adapters`). Tools esperadas pelos prompts atuais (ver
`venus_sdk/prompts/`):
  - personalized_scores, product_scores, product_ingredients  (produto)
  - get_user_allergies                                        (produto/ingrediente/rotina)
  - ingredients, ingredient_effects, ingredient_regulations    (ingrediente)
  - favorites, user_lists, user_profiles                       (rotina)
  - faq_retriever                                              (faq)
"""

from __future__ import annotations

from typing import Any

_mcp_client: Any | None = None


def get_mcp_client() -> Any:
    """Cria (ou reaproveita) o client MCP configurado para o Venus."""
    global _mcp_client
    if _mcp_client is None:
        raise NotImplementedError(
            "TODO: instanciar o client MCP (ex.: MultiServerMCPClient)."
        )
    return _mcp_client


def get_mcp_tools() -> list[Any]:
    """Devolve as tools MCP disponíveis para os agentes especialistas.

    Usada por `flows/agente_mcp.py` para montar o subgrafo ReAct.
    """
    raise NotImplementedError("TODO: carregar as tools a partir do client MCP.")
