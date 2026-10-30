"""Avaliação do casamento alergia × ingrediente e do alerta na resposta final.

Lê `tests/fixtures/avaliacao_alergias.jsonl` (alergia do cadastro, ingrediente
com INCI e nome comum, e se há conflito de verdade) e mede:
- o casamento de `venus_sdk/alergias.py` (o mesmo de `suggest_routine`, do
  Juiz e do Orquestrador): conflitos achados, perdidos e falsos;
- o alerta ponta a ponta: o Orquestrador recebe um texto do LLM que não fala
  da alergia, e a resposta final tem que trazer o alerta quando há conflito.

Não usa LLM nem banco (o LLM do Orquestrador é simulado), então também roda no
CI: `tests/nodes/test_avaliacao_alergias.py` falha se aparecer um erro fora das
lacunas marcadas `"lacuna_conhecida": true`. Resultados em
`docs/avaliacao-alergias.md`.

    python tests/manual/avaliar_alergias.py
    python tests/manual/avaliar_alergias.py --saida-markdown resultado.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parents[2]
FIXTURE = RAIZ / "tests" / "fixtures" / "avaliacao_alergias.jsonl"
_TEXTO_DO_LLM = "O Produto Teste é ótimo para o dia a dia."

from venus_sdk.alergias import termos_da_alergia, termos_em_conflito  # noqa: E402
from venus_sdk.nodes.orquestrador import no_orquestrador  # noqa: E402


def carregar() -> list[dict]:
    return [json.loads(linha) for linha in FIXTURE.read_text(encoding="utf-8").splitlines() if linha.strip()]


def casamento(caso: dict) -> bool:
    return bool(termos_em_conflito(termos_da_alergia(caso["alergia"]), [caso["inci"], caso["comum"]]))


def alerta_na_resposta(caso: dict) -> bool:
    def ev(tool: str, resultado, **argumentos) -> dict:
        return {"tool": tool, "resultado": json.dumps(resultado), "argumentos": argumentos}

    estado = {
        "rota": "produto", "aprovado_juiz": True, "pergunta_original": "esse produto serve pra mim?",
        "evidencias_tools": [
            ev("search_product", [{"product_id": 1, "name": "Produto Teste", "brand_name": "Marca"}]),
            ev("get_product_ingredients", [{"inci_name": "AQUA", "common_name": "Água"},
                                           {"inci_name": caso["inci"], "common_name": caso["comum"]}], product_id=1),
            ev("get_user_allergies", [{"allergy_name": caso["alergia"]}]),
        ],
        "resposta_especialista": {"dominio": "produto", "intencao": "informar", "recomendacao": "",
                                  "resposta": _TEXTO_DO_LLM, "fontes_usadas": ["get_product_ingredients"]},
    }
    with patch("venus_sdk.nodes.orquestrador.get_llm_orquestrador") as get_llm:
        get_llm.return_value.invoke.return_value = SimpleNamespace(content=_TEXTO_DO_LLM)
        return "Atenção:" in no_orquestrador(estado)["resposta_final"]


def avaliar(casos: list[dict]) -> dict:
    perdidos, falsos, alerta_errado = [], [], []
    for caso in casos:
        achou = casamento(caso)
        if caso["conflito"] and not achou:
            perdidos.append(caso)
        elif achou and not caso["conflito"]:
            falsos.append(caso)
        if alerta_na_resposta(caso) != caso["conflito"]:
            alerta_errado.append(caso)
    conflitos = sum(caso["conflito"] for caso in casos)
    return {
        "total": len(casos), "conflitos": conflitos, "achados": conflitos - len(perdidos),
        "perdidos": perdidos, "falsos": falsos, "alerta_errado": alerta_errado,
    }


def _linha(caso: dict) -> str:
    nota = f" ({caso['nota']})" if caso.get("nota") else ""
    lacuna = "" if caso.get("lacuna_conhecida") else " **— fora das lacunas conhecidas**"
    return f"- {caso['alergia']} × {caso['comum']} ({caso['inci']}){nota}{lacuna}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--saida-markdown", type=Path, default=None, metavar="ARQUIVO")
    args = parser.parse_args(argv)

    r = avaliar(carregar())
    sem_conflito = r["total"] - r["conflitos"]
    linhas = [
        "| Medida | Resultado |", "|---|---|",
        f"| Conflitos achados (recall) | {r['achados'] / r['conflitos']:.0%} ({r['achados']}/{r['conflitos']}) |",
        f"| Falsos conflitos | {len(r['falsos'])}/{sem_conflito} |",
        f"| Resposta final com o alerta certo | {r['total'] - len(r['alerta_errado'])}/{r['total']} |",
    ]
    if r["perdidos"]:
        linhas += ["", "Conflitos perdidos:"] + [_linha(caso) for caso in r["perdidos"]]
    if r["falsos"]:
        linhas += ["", "Falsos conflitos:"] + [_linha(caso) for caso in r["falsos"]]
    texto = "\n".join(linhas)
    print(texto)
    if args.saida_markdown:
        args.saida_markdown.write_text(texto + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
