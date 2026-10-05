"""Avaliação do guardrail de entrada (manual — a camada 2 chama o LLM de
verdade, não roda no CI por padrão).

Lê `tests/guardrails/fixtures/injecoes.txt` e `legitimas.txt` e reporta, em
separado:
- camada 1 (regex): injeções bloqueadas e falsos positivos nas legítimas;
- camadas 1 + 2 (regex + classificador LLM): o mesmo, com o classificador
  consultado para o que a regex deixou passar.

As linhas marcadas `# camada2` em `injecoes.txt` passam pela regex de
propósito: são as que só o classificador deve pegar. Sem chave de LLM, a
parte da camada 2 é pulada (e o script diz isso). Resultados registrados em
`docs/avaliacao-guardrail.md`.

    python tests/manual/avaliar_guardrail.py
    python tests/manual/avaliar_guardrail.py --saida-markdown resultado.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
FIXTURES = RAIZ / "tests" / "guardrails" / "fixtures"
MARCA_CAMADA2 = "# camada2"

from venus_sdk.guardrail_rules import guardrail_entrada  # noqa: E402


def carregar(arquivo: str) -> list[tuple[str, bool]]:
    """`[(frase, marcada_camada2)]`, sem as linhas de comentário."""
    frases = []
    for linha in (FIXTURES / arquivo).read_text(encoding="utf-8").splitlines():
        if not linha.strip() or linha.lstrip().startswith("#"):
            continue
        camada2 = linha.rstrip().endswith(MARCA_CAMADA2)
        frases.append((linha.split(MARCA_CAMADA2)[0].strip() if camada2 else linha.strip(), camada2))
    return frases


def _taxa(bloqueadas: int, total: int) -> float:
    return bloqueadas / total if total else 0.0


def _resumo(frases: list[str], bloqueia) -> dict:
    bloqueadas = [frase for frase in frases if bloqueia(frase)]
    return {"total": len(frases), "bloqueadas": len(bloqueadas), "taxa": _taxa(len(bloqueadas), len(frases)),
            "liberadas": [frase for frase in frases if frase not in bloqueadas]}


def _so_regex(frase: str) -> bool:
    return guardrail_entrada(frase)[0]


def avaliar(bloqueia) -> dict:
    injecoes = carregar("injecoes.txt")
    legitimas = [frase for frase, _ in carregar("legitimas.txt")]
    resumo_legitimas = _resumo(legitimas, bloqueia)
    return {
        "injecoes_sem_camada2": _resumo([f for f, camada2 in injecoes if not camada2], bloqueia),
        "injecoes_camada2": _resumo([f for f, camada2 in injecoes if camada2], bloqueia),
        "injecoes_todas": _resumo([f for f, _ in injecoes], bloqueia),
        "legitimas": {**resumo_legitimas, "falsos_positivos": resumo_legitimas["bloqueadas"],
                      "bloqueadas_por_engano": [f for f in legitimas if f not in resumo_legitimas["liberadas"]]},
    }


def avaliar_regex() -> dict:
    return avaliar(_so_regex)


def _tem_llm() -> bool:
    from venus_sdk.llm.models import cadeia_rapida

    return bool(cadeia_rapida())


def _classificar_com_llm(frase: str) -> bool:
    """Só a camada 2, chamada direto (sem o disjuntor esconder falhas)."""
    from venus_sdk.llm.models import extrair_texto_resposta, get_llm_guardrail
    from venus_sdk.prompts.guardrail import GUARDRAIL_LLM_PROMPT

    resposta = get_llm_guardrail().invoke(
        [("system", GUARDRAIL_LLM_PROMPT), ("human", f"<mensagem>\n{frase}\n</mensagem>")]
    )
    veredito = extrair_texto_resposta(resposta).strip().upper()
    return "INJECAO" in veredito or "INJEÇÃO" in veredito


def _modelo_da_camada2() -> str:
    from venus_sdk.llm.models import cadeia_rapida

    itens = cadeia_rapida()
    if not itens:
        return "nenhum (LLM simulado)"
    principal = itens[0]
    reserva = next((item for item in itens[1:] if item[0] != principal[0]), None)
    return ":".join(principal) + (f" (reserva {':'.join(reserva)})" if reserva else "")


def _linhas_da_camada(nome: str, relatorio: dict) -> list[str]:
    def pct(resumo):
        return f"{resumo['taxa']:.0%} ({resumo['bloqueadas']}/{resumo['total']})"

    return [
        f"| {nome} | {pct(relatorio['injecoes_sem_camada2'])} | {pct(relatorio['injecoes_camada2'])} | "
        f"{pct(relatorio['injecoes_todas'])} | {relatorio['legitimas']['falsos_positivos']}/{relatorio['legitimas']['total']} |"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--saida-markdown", type=Path, default=None, metavar="ARQUIVO")
    args = parser.parse_args(argv)

    tabela = ["| Camadas | Injeções (regex) | Injeções `# camada2` | Todas as injeções | Falsos positivos |",
              "|---|---|---|---|---|"]
    regex = avaliar_regex()
    tabela += _linhas_da_camada("camada 1: regex", regex)
    avisos: list[str] = []
    detalhes = [f"- escapou da regex: {f}" for f in regex["injecoes_todas"]["liberadas"]]

    if _tem_llm():
        erros: list[str] = []

        def regex_e_llm(frase: str) -> bool:
            if _so_regex(frase):
                return True
            try:
                return _classificar_com_llm(frase)
            except Exception as erro:  # noqa: BLE001 — falha do LLM conta como "passou" (fail-open)
                erros.append(type(erro).__name__)
                return False

        duas = avaliar(regex_e_llm)
        tabela += _linhas_da_camada("camadas 1 + 2: regex + LLM", duas)
        detalhes += [f"- escapou das duas camadas: {f}" for f in duas["injecoes_todas"]["liberadas"]]
        detalhes += [f"- legítima bloqueada (regex + LLM): {f}" for f in duas["legitimas"]["bloqueadas_por_engano"]]
        avisos.append(f"Modelo da camada 2: {_modelo_da_camada2()}.")
        if erros:
            avisos.append(f"{len(erros)} chamada(s) ao LLM falharam e contaram como liberadas: {sorted(set(erros))}.")
    else:
        avisos.append("Camada 2 (regex + LLM) não avaliada: sem chave de LLM (MISTRAL_API_KEY, GROQ_API_KEY ou "
                      "GEMINI_API_KEY). Só a camada 1 foi medida.")

    texto = "\n".join(tabela + [""] + avisos + ([""] + detalhes if detalhes else []))
    print(texto)
    if args.saida_markdown:
        args.saida_markdown.write_text(texto + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
