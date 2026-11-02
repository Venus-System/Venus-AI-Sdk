import sys
from pathlib import Path

# Permite `from _fakes import ...` e importar os scripts de tests/manual em
# qualquer subpasta de tests/.
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "manual"))

import os  # noqa: E402

# A suíte roda offline: o índice local do FAQ usa o EmbeddingsHash em vez de
# baixar o modelo do FastEmbed (vale também para os subprocessos, ex.: o
# servidor MCP por stdio). Os testes do caminho semântico trocam a função.
os.environ.setdefault("VENUS_EMBEDDINGS_LOCAIS", "hash")
# O caminho com LLM segue valendo para Rotina e FAQ e é o que estes testes cobrem;
# `tests/nodes/test_resposta_so_das_tools.py` cobre o padrão.
os.environ.setdefault("VENUS_RESPOSTA_SO_DAS_TOOLS", "")
# O classificador LLM do guardrail fica ligado por padrão em produção; na
# suíte ele consumiria as respostas roteirizadas dos LLMs falsos. Os testes
# dele (tests/guardrails/test_classificador_llm.py) ligam explicitamente.
os.environ.setdefault("VENUS_GUARDRAIL_LLM", "0")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _classificador_do_guardrail_zerado():
    """Contadores e disjuntor do classificador do guardrail são por processo:
    sem zerar, as falhas de um teste abririam o disjuntor para os seguintes."""
    from venus_sdk.nodes import guardrails

    guardrails._reiniciar_classificador()
    yield
    guardrails._reiniciar_classificador()
