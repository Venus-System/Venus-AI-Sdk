"""Testes do nó Agente Juiz e da aresta condicional decidir_pos_juiz."""

import pytest

from venus_sdk.nodes.juiz import decidir_pos_juiz, no_agente_juiz


def test_no_agente_juiz_ainda_nao_implementado() -> None:
    # TODO: substituir por um teste real quando `no_agente_juiz` for implementado.
    with pytest.raises(NotImplementedError):
        no_agente_juiz({})  # type: ignore[arg-type]


def test_decidir_pos_juiz_ainda_nao_implementado() -> None:
    # TODO: substituir por um teste real quando `decidir_pos_juiz` for implementado.
    with pytest.raises(NotImplementedError):
        decidir_pos_juiz({})  # type: ignore[arg-type]
