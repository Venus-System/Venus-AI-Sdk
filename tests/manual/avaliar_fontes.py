"""Avaliação de afirmações sem fonte (manual — LLMs e Postgres de verdade, só leitura).

Roda cada pergunta de `tests/fixtures/avaliacao_fontes.jsonl` pelo grafo e mede:
- respostas conhecidas: a nota geral do produto e se ele tem fragrância, valores
  tirados do catálogo e congelados no fixture;
- afirmações sem fonte: um auditor LLM lista as afirmações factuais da resposta e
  confere cada uma contra o retorno das tools do turno. Ele também erra, então o
  JSON gravado traz cada afirmação marcada, para revisão humana.

Critério do projeto: nenhuma afirmação sem fonte e 100% nas respostas conhecidas.

    python tests/manual/avaliar_fontes.py --saida-json fontes.json
    python tests/manual/avaliar_fontes.py --ler-json fontes.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]
FIXTURE = RAIZ / "tests" / "fixtures" / "avaliacao_fontes.jsonl"
TIPOS = ("nota", "fragrancia", "armadilha", "aberta")
_LIMITE_EVIDENCIAS = 24_000

PROMPT_AUDITOR = """Você audita respostas de uma assistente de skincare.
Liste cada afirmação factual da RESPOSTA sobre produto, ingrediente, efeito, segurança,
modo de uso ou sobre o próprio sistema. Ignore saudações, ofertas de ajuda e frases que
só dizem que um dado não existe ou não foi encontrado.
Para cada afirmação, diga se os RESULTADOS_TOOLS a sustentam. Sustenta é ter o fato lá,
mesmo com outras palavras. Conhecimento geral, mesmo correto, NÃO sustenta.
Responda só com JSON: {"afirmacoes": [{"texto": "...", "sustentada": true, "motivo": "..."}]}"""


def carregar() -> list[dict]:
    return [json.loads(linha) for linha in FIXTURE.read_text(encoding="utf-8").splitlines() if linha.strip()]


def acertou_conhecida(caso: dict, resposta: str) -> bool | None:
    """Confere a resposta conhecida; `None` nos tipos sem resposta conhecida."""
    if caso["tipo"] == "nota":
        return re.search(rf"(?<![\d,.]){caso['nota']}(?![\d])", resposta) is not None
    if caso["tipo"] == "fragrancia":
        # Só a 1ª frase: "tem fragrância. Se prefere sem fragrância..." afirma.
        primeira = re.split(r"[.\n]", resposta.strip(), maxsplit=1)[0]
        nega = re.search(r"\b(não|nao|sem)\b.{0,40}\b(fragr|parfum|perfume)", primeira, re.IGNORECASE) is not None
        afirma = re.search(r"\b(fragr|parfum)", primeira, re.IGNORECASE) is not None and not nega
        return afirma if caso["tem_fragrancia"] else nega
    return None


def ler_auditoria(texto: str) -> list[dict]:
    from venus_sdk.nodes._json import extrair_objeto_json

    try:
        afirmacoes = extrair_objeto_json(texto).get("afirmacoes")
    except ValueError:
        return []
    return [a for a in afirmacoes if isinstance(a, dict)] if isinstance(afirmacoes, list) else []


def auditar(resposta: str, evidencias: list[dict] | None) -> list[dict] | None:
    """Afirmações da resposta (`None` se o auditor falhar)."""
    from venus_sdk.llm.models import extrair_texto_resposta, get_llm_juiz

    entrada = (f"RESULTADOS_TOOLS={json.dumps(evidencias or [], ensure_ascii=False)[:_LIMITE_EVIDENCIAS]}\n\n"
               f"RESPOSTA={resposta}")
    try:
        saida = extrair_texto_resposta(get_llm_juiz().invoke([("system", PROMPT_AUDITOR), ("human", entrada)]))
    except Exception:  # noqa: BLE001 — turno sem auditoria é contado à parte
        return None
    return ler_auditoria(saida) if "afirmacoes" in saida else None


async def medir(casos: list[dict]) -> list[dict]:
    import asyncpg

    from venus_sdk.config.settings import DATABASE_URL, FAQ_DIR
    from venus_sdk.flows.venus_flow import compilar_grafo_venus
    from venus_sdk.memory import criar_checkpointer_em_memoria, criar_store_em_memoria
    from venus_sdk.rag import criar_indice_local

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
    grafo = compilar_grafo_venus(
        checkpointer=criar_checkpointer_em_memoria(), store=criar_store_em_memoria(), pool=pool,
        indice_rag=criar_indice_local(FAQ_DIR),
    )
    resultados = []
    try:
        for caso in casos:
            config = {"configurable": {"thread_id": f"fontes-{uuid.uuid4().hex[:8]}"}, "recursion_limit": 40}
            entrada = {"mensagem_usuario": caso["pergunta"], "usuario_id": "fontes", "usuario_id_postgres": 1}
            try:
                final = await asyncio.wait_for(grafo.ainvoke(entrada, config=config), 240)
            except Exception as exc:  # noqa: BLE001
                final = {"resposta_final": "", "erro": type(exc).__name__}
            resposta = final.get("resposta_final") or ""
            resultados.append({
                **caso, "resposta": resposta, "rota": final.get("rota"), "erro": final.get("erro"),
                "aprovado_juiz": final.get("aprovado_juiz"), "acertou": acertou_conhecida(caso, resposta),
                "afirmacoes": await asyncio.to_thread(auditar, resposta, final.get("evidencias_tools")),
                "evidencias": final.get("evidencias_tools"),
            })
            print(f"{caso['tipo']:10} {caso['pergunta'][:50]!r} rota={final.get('rota')}", flush=True)
    finally:
        await pool.close()
    return resultados


def resumo(resultados: list[dict]) -> str:
    from avaliar_alergias import intervalo

    def taxa(acertos: int, total: int) -> str:
        if not total:
            return "-"
        baixo, alto = intervalo(acertos, total)
        return f"{acertos}/{total} (IC 95%: {baixo:.0%} a {alto:.0%})"

    auditadas = [r for r in resultados if r["afirmacoes"] is not None]
    limpas = [r for r in auditadas if all(a.get("sustentada") for a in r["afirmacoes"])]
    linhas = ["| Medida | Resultado |", "|---|---|"]
    for tipo in ("nota", "fragrancia"):
        casos = [r for r in resultados if r["tipo"] == tipo]
        linhas.append(f"| Resposta conhecida certa: {tipo} | {taxa(sum(bool(r['acertou']) for r in casos), len(casos))} |")
    linhas.append(f"| Respostas sem afirmação sem fonte | {taxa(len(limpas), len(auditadas))} |")
    for tipo in TIPOS:
        casos = [r for r in auditadas if r["tipo"] == tipo]
        limpos = [r for r in casos if all(a.get("sustentada") for a in r["afirmacoes"])]
        linhas.append(f"| Sem afirmação sem fonte: {tipo} | {taxa(len(limpos), len(casos))} |")
    linhas.append(f"| Não auditadas (falha do auditor) | {len(resultados) - len(auditadas)} |")
    linhas += ["", "Afirmações marcadas (revisar à mão):"]
    for r in auditadas:
        for a in r["afirmacoes"]:
            if not a.get("sustentada"):
                linhas.append(f"- [{r['pergunta'][:40]}] {a.get('texto')}")
    errados = [r for r in resultados if r["acertou"] is False]
    if errados:
        linhas += ["", "Respostas conhecidas erradas:"]
        linhas += [f"- {r['pergunta']} → {r['resposta'][:160]!r}" for r in errados]
    return "\n".join(linhas)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--saida-json", type=Path, default=None, metavar="ARQUIVO")
    parser.add_argument("--ler-json", type=Path, default=None, metavar="ARQUIVO")
    args = parser.parse_args(argv)

    if args.ler_json:
        resultados: Any = json.loads(args.ler_json.read_text(encoding="utf-8"))
    else:
        from dotenv import load_dotenv

        load_dotenv(RAIZ / ".env")
        resultados = asyncio.run(medir(carregar()))
        if args.saida_json:
            args.saida_json.write_text(json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8")
    print(resumo(resultados))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
