"""Avaliação do RAG do FAQ (manual — usa o modelo FastEmbed de verdade, não
roda no CI).

Para cada pergunta de `tests/fixtures/avaliacao_rag.jsonl`:
- com resposta no FAQ: hit rate@3 (alguma fonte esperada entre os 3 trechos)
  e MRR (1/posição do primeiro trecho certo);
- sem resposta no FAQ: "não sei" correto = nenhum trecho acima do corte.

Roda para vários cortes de relevância, para escolher `_SCORE_MINIMO`
(`rag/faq.py`):

    python tests/manual/avaliar_rag.py                    # índice local semântico
    python tests/manual/avaliar_rag.py --indice qdrant    # Qdrant em memória (mesma ingestão de produção)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
PERGUNTAS = RAIZ / "tests" / "fixtures" / "avaliacao_rag.jsonl"
from venus_sdk.config.settings import FAQ_DIR  # noqa: E402

FAQ = Path(FAQ_DIR)
CORTES = (0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5)
_K = 3


def _indice(tipo: str):
    from venus_sdk.rag.faq import EmbeddingsFastEmbed, IndiceQdrant
    from venus_sdk.rag.indice import criar_indice_local
    from venus_sdk.rag.vector_build import get_embed_model

    if tipo == "local":
        return criar_indice_local(FAQ, EmbeddingsFastEmbed(get_embed_model()))
    from qdrant_client import QdrantClient

    from venus_sdk.rag.faq_ingest import ingerir_faq

    cliente = QdrantClient(":memory:")
    ingerir_faq(FAQ, cliente=cliente)
    return IndiceQdrant(cliente=cliente)


def avaliar(indice, perguntas: list[dict], corte: float) -> dict[str, float]:
    acertos, reciprocos, nao_sei_certos, com_resposta, sem_resposta = 0, 0.0, 0, 0, 0
    for item in perguntas:
        achados = indice.buscar(item["pergunta"], k=_K, score_minimo=corte)
        fontes = [achado["fonte"] for achado in achados]
        if not item["fontes"]:
            sem_resposta += 1
            nao_sei_certos += not fontes
            continue
        com_resposta += 1
        posicoes = [i for i, fonte in enumerate(fontes, 1) if fonte in item["fontes"]]
        acertos += bool(posicoes)
        reciprocos += 1 / posicoes[0] if posicoes else 0
    return {
        "hit@3": acertos / com_resposta,
        "mrr": reciprocos / com_resposta,
        "nao_sei_correto": nao_sei_certos / sem_resposta,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--indice", choices=["local", "qdrant"], default="local")
    args = parser.parse_args()
    perguntas = [json.loads(linha) for linha in PERGUNTAS.read_text(encoding="utf-8").splitlines() if linha.strip()]
    indice = _indice(args.indice)
    print(f"índice: {args.indice} | {sum(1 for p in perguntas if p['fontes'])} com resposta, "
          f"{sum(1 for p in perguntas if not p['fontes'])} sem resposta")
    print(f"{'corte':>6} {'hit@3':>7} {'MRR':>6} {'não sei certo':>14}")
    for corte in CORTES:
        m = avaliar(indice, perguntas, corte)
        print(f"{corte:>6.2f} {m['hit@3']:>7.0%} {m['mrr']:>6.2f} {m['nao_sei_correto']:>14.0%}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
