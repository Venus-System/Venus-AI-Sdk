import sys
from pathlib import Path

# Permite `from _fakes import ...` em qualquer subpasta de tests/.
sys.path.insert(0, str(Path(__file__).parent))

import os  # noqa: E402

# A suíte roda offline: o índice local do FAQ usa o EmbeddingsHash em vez de
# baixar o modelo do FastEmbed (vale também para os subprocessos, ex.: o
# servidor MCP por stdio). Os testes do caminho semântico trocam a função.
os.environ.setdefault("VENUS_EMBEDDINGS_LOCAIS", "hash")
