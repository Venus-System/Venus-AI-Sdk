"""Avaliação do RAG do FAQ (manual — com FastEmbed usa o modelo de verdade,
não roda no CI).

Para cada pergunta de `tests/fixtures/avaliacao_rag.jsonl`:
- com resposta no FAQ: hit rate@3 (alguma fonte esperada entre os 3 trechos)
  e MRR (1/posição do primeiro trecho certo);
- sem resposta no FAQ: "não sei" correto = nenhum trecho acima do corte.

Roda para vários cortes de relevância, para escolher `_SCORE_MINIMO`
(`rag/faq.py`). Resultados registrados em `docs/avaliacao-rag.md`.

    python tests/manual/avaliar_rag.py                        # índice local + FastEmbed (o da API sem Qdrant)
    python tests/manual/avaliar_rag.py --embeddings hash      # índice local + EmbeddingsHash (fallback sem o modelo)
    python tests/manual/avaliar_rag.py --indice qdrant        # Qdrant em memória (mesma ingestão de produção)
    python tests/manual/avaliar_rag.py --faq-dir outra/pasta --detalhes
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
PERGUNTAS = RAIZ / "tests" / "fixtures" / "avaliacao_rag.jsonl"
from venus_sdk.config.settings import FAQ_DIR  # noqa: E402

# Os scores do hash (contagem de palavras) ficam bem abaixo dos do FastEmbed
# (cosseno de embeddings semânticos), então cada um tem a sua faixa de cortes.
CORTES = {
    "fastembed": (0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5),
    "hash": (0.05, 0.1, 0.15, 0.2, 0.25, 0.3),
}
_K = 3


def carregar_perguntas() -> list[dict]:
    return [json.loads(linha) for linha in PERGUNTAS.read_text(encoding="utf-8").splitlines() if linha.strip()]


def criar_indice(tipo: str, embeddings: str, pasta: Path):
    """Índice local (o que a API usa sem `QDRANT_URL`) ou Qdrant em memória."""
    from venus_sdk.rag.indice import criar_indice_local

    if embeddings == "hash":
        from venus_sdk.rag.indice import EmbeddingsHash

        return criar_indice_local(pasta, EmbeddingsHash())
    from venus_sdk.rag.faq import EmbeddingsFastEmbed, IndiceQdrant
    from venus_sdk.rag.vector_build import get_embed_model

    if tipo == "local":
        return criar_indice_local(pasta, EmbeddingsFastEmbed(get_embed_model()))
    from qdrant_client import QdrantClient

    from venus_sdk.rag.faq_ingest import ingerir_faq

    cliente = QdrantClient(":memory:")
    ingerir_faq(pasta, cliente=cliente)
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


def _imprimir_detalhes(indice, perguntas: list[dict], corte: float) -> None:
    print(f"\nTop {_K} por pergunta (corte {corte}):")
    for item in perguntas:
        achados = indice.buscar(item["pergunta"], k=_K, score_minimo=corte)
        fontes = [achado["fonte"] for achado in achados]
        certo = not fontes if not item["fontes"] else any(fonte in item["fontes"] for fonte in fontes)
        listagem = ", ".join(f"{a['fonte']} ({a['score']})" for a in achados) or "(nada)"
        print(f"  {'ok ' if certo else 'ERR'} {item['pergunta']} -> {listagem}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--indice", choices=["local", "qdrant"], default="local")
    parser.add_argument("--embeddings", choices=["fastembed", "hash"], default="fastembed")
    parser.add_argument("--faq-dir", type=Path, default=Path(FAQ_DIR))
    parser.add_argument("--detalhes", type=float, nargs="?", const=-1.0, default=None, metavar="CORTE",
                        help="lista o top 3 de cada pergunta (sem valor: o corte de produção)")
    parser.add_argument("--minimo-hit3", type=float, default=None, metavar="X",
                        help="sai com código 1 se o hit@3 no corte de produção ficar abaixo de X (barra regressão no CI)")
    parser.add_argument("--saida-markdown", type=Path, default=None, metavar="ARQUIVO",
                        help="também grava a tabela em markdown (resumo do job no CI)")
    args = parser.parse_args(argv)
    if args.indice == "qdrant" and args.embeddings == "hash":
        parser.error("o Qdrant sempre usa FastEmbed; --embeddings hash só com --indice local")

    perguntas = carregar_perguntas()
    indice = criar_indice(args.indice, args.embeddings, args.faq_dir)
    producao = _corte_de_producao(args.embeddings)
    print(f"índice: {args.indice} | embeddings: {args.embeddings} | FAQ: {args.faq_dir}")
    print(f"{sum(1 for p in perguntas if p['fontes'])} perguntas com resposta, "
          f"{sum(1 for p in perguntas if not p['fontes'])} sem resposta")
    print(f"{'corte':>6} {'hit@3':>7} {'MRR':>6} {'não sei certo':>14}")
    linhas = []
    for corte in CORTES[args.embeddings]:
        m = avaliar(indice, perguntas, corte)
        linhas.append((corte, m))
        print(f"{corte:>6.2f} {m['hit@3']:>7.0%} {m['mrr']:>6.2f} {m['nao_sei_correto']:>14.0%}")
    if args.saida_markdown:
        args.saida_markdown.write_text(_tabela_markdown(f"{args.indice} + {args.embeddings}", linhas, producao),
                                       encoding="utf-8")
    if args.detalhes is not None:
        _imprimir_detalhes(indice, perguntas, producao if args.detalhes < 0 else args.detalhes)
    if args.minimo_hit3 is not None:
        hit3 = avaliar(indice, perguntas, producao)["hit@3"]
        if hit3 < args.minimo_hit3:
            print(f"\nhit@3 {hit3:.0%} no corte de produção ({producao}) abaixo do mínimo de {args.minimo_hit3:.0%}.")
            return 1
        print(f"\nhit@3 {hit3:.0%} no corte de produção ({producao}): dentro do mínimo de {args.minimo_hit3:.0%}.")
    return 0


def _corte_de_producao(embeddings: str) -> float:
    from venus_sdk.rag.faq import _SCORE_MINIMO
    from venus_sdk.rag.indice import SCORE_MINIMO_HASH

    return SCORE_MINIMO_HASH if embeddings == "hash" else _SCORE_MINIMO


def _tabela_markdown(configuracao: str, linhas: list[tuple[float, dict[str, float]]], producao: float) -> str:
    saida = [f"### RAG do FAQ: {configuracao}", "", "| Corte | hit@3 | MRR | \"não sei\" correto |", "|---|---|---|---|"]
    for corte, m in linhas:
        rotulo = f"**{corte:.2f}**" if abs(corte - producao) < 1e-9 else f"{corte:.2f}"
        saida.append(f"| {rotulo} | {m['hit@3']:.0%} | {m['mrr']:.2f} | {m['nao_sei_correto']:.0%} |")
    saida += ["", f"Em negrito, o corte de produção ({producao})."]
    return "\n".join(saida) + "\n"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
