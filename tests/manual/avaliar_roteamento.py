"""Avaliação do roteador (manual — chama o LLM de verdade, não roda no CI).

Lê `tests/fixtures/avaliacao_roteamento.jsonl` (pergunta, rotas aceitas,
categoria) e mede a taxa de acerto da rota, por categoria, em duas versões:
- só palavras-chave (`rota_por_palavras`, sem LLM): a linha de base;
- o nó roteador completo (`no_roteador`: LLM + redes de segurança).

"direto" é a resposta do próprio roteador (saudação, fora de escopo). Em
`alergia_no_chat`, a resposta direta só conta se for a resposta fixa que manda
atualizar o cadastro. Em `mista`, qualquer uma das rotas aceitas conta, mas o
grafo só chama um especialista: a outra metade da pergunta fica sem resposta.
Sem chave de LLM, mede só as palavras-chave. Resultados registrados em
`docs/avaliacao-roteamento.md`.

    python tests/manual/avaliar_roteamento.py
    python tests/manual/avaliar_roteamento.py --sem-llm
    python tests/manual/avaliar_roteamento.py --saida-markdown resultado.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
FIXTURE = RAIZ / "tests" / "fixtures" / "avaliacao_roteamento.jsonl"
CATEGORIAS = ("produto", "ingrediente", "rotina", "faq", "direto", "mista", "alergia_no_chat")

from venus_sdk.nodes.roteador import _RESPOSTA_ALERGIA_NO_CHAT, no_roteador, rota_por_palavras  # noqa: E402


def carregar() -> list[dict]:
    return [json.loads(linha) for linha in FIXTURE.read_text(encoding="utf-8").splitlines() if linha.strip()]


def acertou(caso: dict, rota: str, resposta: str | None) -> bool:
    if rota not in caso["rotas"]:
        return False
    if caso["categoria"] == "alergia_no_chat" and rota == "direto" and resposta is not None:
        # Só o nó roteador escreve resposta; as palavras-chave só escolhem a rota.
        return resposta == _RESPOSTA_ALERGIA_NO_CHAT
    return True


def avaliar(casos: list[dict], rotear) -> dict:
    """`rotear(pergunta) -> (rota, resposta_direta)`. Devolve acertos por categoria e os erros."""
    por_categoria: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    erros = []
    for caso in casos:
        rota, resposta = rotear(caso["pergunta"])
        ok = acertou(caso, rota, resposta)
        por_categoria[caso["categoria"]][0] += ok
        por_categoria[caso["categoria"]][1] += 1
        if not ok:
            erros.append({"pergunta": caso["pergunta"], "esperado": caso["rotas"], "obtido": rota})
    acertos = sum(a for a, _ in por_categoria.values())
    return {"acertos": acertos, "total": len(casos), "por_categoria": dict(por_categoria), "erros": erros}


def rotear_por_palavras(pergunta: str) -> tuple[str, None]:
    return rota_por_palavras(pergunta) or "direto", None


def rotear_com_llm(pergunta: str) -> tuple[str, str | None]:
    resultado = no_roteador({"mensagem_usuario": pergunta})
    return resultado.get("rota") or "direto", resultado.get("resposta_final")


def _tem_llm() -> bool:
    from venus_sdk.llm.models import cadeia_rapida

    return bool(cadeia_rapida())


def _modelo() -> str:
    from venus_sdk.llm.models import cadeia_rapida

    return " → ".join(":".join(item) for item in cadeia_rapida()) or "nenhum"


def _pct(acertos: int, total: int) -> str:
    return f"{acertos / total:.0%} ({acertos}/{total})" if total else "-"


def tabela(relatorios: dict[str, dict]) -> list[str]:
    nomes = list(relatorios)
    linhas = ["| Categoria | " + " | ".join(nomes) + " |", "|---|" + "---|" * len(nomes)]
    for categoria in CATEGORIAS:
        celulas = [_pct(*relatorios[nome]["por_categoria"].get(categoria, (0, 0))) for nome in nomes]
        linhas.append(f"| {categoria} | " + " | ".join(celulas) + " |")
    linhas.append("| **total** | " + " | ".join(
        f"**{_pct(relatorios[nome]['acertos'], relatorios[nome]['total'])}**" for nome in nomes) + " |")
    return linhas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sem-llm", action="store_true", help="mede só as palavras-chave")
    parser.add_argument("--saida-markdown", type=Path, default=None, metavar="ARQUIVO")
    args = parser.parse_args(argv)

    casos = carregar()
    relatorios = {"palavras-chave": avaliar(casos, rotear_por_palavras)}
    avisos = []
    if not args.sem_llm and _tem_llm():
        relatorios["roteador (LLM + regras)"] = avaliar(casos, rotear_com_llm)
        avisos.append(f"Cadeia do roteador: {_modelo()}.")
    else:
        avisos.append("Roteador com LLM não avaliado (--sem-llm ou sem MISTRAL/GROQ/GEMINI_API_KEY).")

    final = list(relatorios)[-1]
    detalhes = [f"- {e['pergunta']} → esperado {'/'.join(e['esperado'])}, veio {e['obtido']}"
                for e in relatorios[final]["erros"]]
    texto = "\n".join(tabela(relatorios) + [""] + avisos
                      + ([f"", f"Erros ({final}):"] + detalhes if detalhes else []))
    print(texto)
    if args.saida_markdown:
        args.saida_markdown.write_text(texto + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
