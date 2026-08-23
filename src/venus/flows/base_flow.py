"""Classe base para os fluxos (pipelines) de agentes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseFlow(ABC):
    """Contrato mínimo que todo fluxo de orquestração deve implementar."""

    name: str = "base_flow"

    @abstractmethod
    def execute(self, *args: Any, **kwargs: Any) -> Any:
        """Executa o fluxo, coordenando um ou mais agentes."""
        raise NotImplementedError
