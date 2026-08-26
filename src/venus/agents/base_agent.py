"""Classe base para os agentes do SDK Venus."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseAgent(ABC):
    """Contrato mínimo que todo agente do sistema deve implementar."""

    name: str = "base_agent"

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> Any:
        """Executa a tarefa do agente."""
        raise NotImplementedError
