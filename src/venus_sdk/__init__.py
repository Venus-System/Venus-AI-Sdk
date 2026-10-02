"""SDK da IA da Venus: agentes, fluxo LangGraph, tools, RAG, MCP e A2A."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Lida dos metadados do pacote instalado, então é sempre a do
    # `pyproject.toml` que gerou a instalação (API e logs sabem qual SDK roda).
    __version__ = version("venus-ai-sdk")
except PackageNotFoundError:  # rodando de um checkout sem `pip install`
    __version__ = "0.0.0+desconhecida"
