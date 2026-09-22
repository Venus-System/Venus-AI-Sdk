"""Dublês de teste compartilhados: LLM com script (suporta tool calls) e pool asyncpg falso."""

from __future__ import annotations

from typing import Any, Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class LLMScript(BaseChatModel):
    """LLM falso: devolve as respostas do script em ordem (a última se repete).
    Cada item é uma `AIMessage` (inclusive com `tool_calls`) ou um callable
    `(mensagens) -> AIMessage`, para respostas que dependem da entrada."""

    script: list[Any]
    chamadas: list[list[BaseMessage]] = []
    _i: int = 0

    @property
    def _llm_type(self) -> str:
        return "llm-script"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "LLMScript":
        return self

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None,
                  **kwargs: Any) -> ChatResult:
        self.chamadas.append(messages)
        item = self.script[min(self._i, len(self.script) - 1)]
        self._i += 1
        msg = item(messages) if callable(item) else item
        return ChatResult(generations=[ChatGeneration(message=msg)])


def chamada_tool(nome: str, args: dict, id_: str = "c1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": nome, "args": args, "id": id_}])


class ConexaoFalsa:
    def __init__(self, fetch: Any = None, fetchrow: Any = None, execute: Any = "OK", erro: Exception | None = None):
        self._fetch, self._fetchrow, self._execute, self._erro = fetch, fetchrow, execute, erro
        self.chamadas: list[tuple[str, tuple]] = []

    def _r(self, v: Any, q: str, a: tuple):
        self.chamadas.append((q, a))
        if self._erro:
            raise self._erro
        return v(q, *a) if callable(v) else v

    async def fetch(self, q: str, *a: Any):
        return self._r(self._fetch if self._fetch is not None else [], q, a)

    async def fetchrow(self, q: str, *a: Any):
        return self._r(self._fetchrow, q, a)

    async def execute(self, q: str, *a: Any):
        return self._r(self._execute, q, a)


class PoolFalso:
    def __init__(self, conexao: ConexaoFalsa):
        self.conexao = conexao

    def acquire(self):
        conexao = self.conexao

        class _Ctx:
            async def __aenter__(self_):
                return conexao

            async def __aexit__(self_, *exc):
                return False

        return _Ctx()
