"""Avaliação do casamento alergia × ingrediente e do alerta na resposta final.

Duas bases:
- `tests/fixtures/avaliacao_alergias.jsonl`: pares escritos à mão (alergia,
  ingrediente com INCI e nome comum, e se há conflito de verdade), com casos
  difíceis de propósito;
- `tests/fixtures/avaliacao_alergias_catalogo.json`: as 18 alergias do catálogo
  de produção × os 515 ingredientes usados em produtos, com o gabarito de
  conflitos definido por regras escritas à parte (não usa `alergias.py`).

Mede:
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
import math
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parents[2]
FIXTURE = RAIZ / "tests" / "fixtures" / "avaliacao_alergias.jsonl"
FIXTURE_CATALOGO = RAIZ / "tests" / "fixtures" / "avaliacao_alergias_catalogo.json"
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


def intervalo(acertos: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de confiança de 95% (Wilson) para a proporção `acertos/total`.
    Com 0 erros em 47 casos, a taxa real de erro ainda pode chegar a uns 7%."""
    if not total:
        return 0.0, 1.0
    p = acertos / total
    centro = (p + z * z / (2 * total)) / (1 + z * z / total)
    margem = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return max(0.0, centro - margem), min(1.0, centro + margem)


def _taxa(acertos: int, total: int) -> str:
    baixo, alto = intervalo(acertos, total)
    return f"{acertos / total:.1%} ({acertos}/{total}; IC 95%: {baixo:.1%} a {alto:.1%})" if total else "-"


def avaliar_catalogo() -> dict:
    """Cada alergia do catálogo × cada ingrediente usado em produtos."""
    dados = json.loads(FIXTURE_CATALOGO.read_text(encoding="utf-8"))
    perdidos, falsos, pares = [], [], 0
    for alergia in dados["alergias"]:
        termos = termos_da_alergia(alergia)
        esperados = set(dados["conflitos"][alergia])
        for ingrediente in dados["ingredientes"]:
            pares += 1
            nome = ingrediente["inci"] or ingrediente["comum"]
            achou = bool(termos_em_conflito(termos, [ingrediente["inci"], ingrediente["comum"]]))
            if nome in esperados and not achou:
                perdidos.append((alergia, nome))
            elif achou and nome not in esperados:
                falsos.append((alergia, nome))
    conflitos = sum(len(lista) for lista in dados["conflitos"].values())
    return {"pares": pares, "conflitos": conflitos, "achados": conflitos - len(perdidos),
            "perdidos": perdidos, "falsos": falsos}


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
        "Casos escritos à mão:", "",
        "| Medida | Resultado |", "|---|---|",
        f"| Conflitos achados (recall) | {_taxa(r['achados'], r['conflitos'])} |",
        f"| Sem conflito e sem alerta | {_taxa(sem_conflito - len(r['falsos']), sem_conflito)} |",
        f"| Resposta final com o alerta certo | {_taxa(r['total'] - len(r['alerta_errado']), r['total'])} |",
    ]
    if r["perdidos"]:
        linhas += ["", "Conflitos perdidos:"] + [_linha(caso) for caso in r["perdidos"]]
    if r["falsos"]:
        linhas += ["", "Falsos conflitos:"] + [_linha(caso) for caso in r["falsos"]]

    c = avaliar_catalogo()
    sem_conflito = c["pares"] - c["conflitos"]
    linhas += [
        "", "Catálogo de produção (alergias × ingredientes usados em produtos):", "",
        "| Medida | Resultado |", "|---|---|",
        f"| Pares avaliados | {c['pares']} |",
        f"| Conflitos achados (recall) | {_taxa(c['achados'], c['conflitos'])} |",
        f"| Sem conflito e sem alerta | {_taxa(sem_conflito - len(c['falsos']), sem_conflito)} |",
    ]
    linhas += [f"- perdido: {alergia} × {nome}" for alergia, nome in c["perdidos"]]
    linhas += [f"- falso: {alergia} × {nome}" for alergia, nome in c["falsos"]]
    texto = "\n".join(linhas)
    print(texto)
    if args.saida_markdown:
        args.saida_markdown.write_text(texto + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
