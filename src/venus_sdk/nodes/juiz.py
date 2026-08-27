"""Nó Agente Juiz: valida a saída dos especialistas antes do orquestrador."""

from __future__ import annotations

from typing import Literal

from venus_sdk.state import EstadoVenus

ResultadoJuiz = Literal["aprovado", "reprovado", "esgotado"]


def no_agente_juiz(estado: EstadoVenus) -> EstadoVenus:
    """Avalia `resposta_especialista` e atualiza `aprovado_juiz`,
    `feedback_juiz` e `tentativas_juiz`.

    TODO: implementar a chamada ao LLM juiz e o critério de validação.
    """
    raise NotImplementedError


def decidir_pos_juiz(estado: EstadoVenus) -> ResultadoJuiz:
    """Aresta condicional pós-juiz.

    Deve devolver "reprovado" enquanto houver tentativas disponíveis (volta
    para o roteador/especialista), e "esgotado" quando as tentativas
    acabarem (segue para o orquestrador mesmo sem aprovação total).

    TODO: implementar — deve casar com as chaves usadas em
    `flows/venus_flow.py`.
    """
    raise NotImplementedError
