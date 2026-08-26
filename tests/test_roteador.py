"""Testes do nó Roteador e da aresta condicional decidir_especialista."""

import pytest

from venus_sdk.nodes.roteador import decidir_especialista, no_roteador


def test_no_roteador_ainda_nao_implementado() -> None:
    # TODO: substituir por um teste real quando `no_roteador` for implementado.
    with pytest.raises(NotImplementedError):
        no_roteador({})  # type: ignore[arg-type]


def test_decidir_especialista_ainda_nao_implementado() -> None:
    # TODO: substituir por um teste real quando `decidir_especialista` for implementado.
    with pytest.raises(NotImplementedError):
        decidir_especialista({})  # type: ignore[arg-type]
