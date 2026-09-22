"""Utilitários comuns às tools Postgres: normalização de busca, execução
segura de queries e respostas estruturadas de "não encontrado"/erro.

Objetivo: uma tool NUNCA devolve lista vazia silenciosa nem estoura exceção
dentro do agente ReAct — sempre uma estrutura explícita, para que o
especialista e o Agente Juiz não inventem dados nem quebrem."""

from __future__ import annotations

import logging
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

LIMITE_BUSCA = 10

# Tradução SQL pura (sem extensão `unaccent`) — o mesmo mapa é aplicado ao
# termo no Python (`normalizar_termo`) e à coluna no SQL (`sem_acento`), então
# "hialuronico" casa com "Hialurônico" em qualquer Postgres.
_COM_ACENTO = "áàâãäéèêëíìîïóòôõöúùûüçñÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑ"
_SEM_ACENTO = "aaaaaeeeeiiiiooooouuuucnAAAAAEEEEIIIIOOOOOUUUUCN"


def normalizar_termo(termo: str) -> str:
    """Tira acento e espaços das pontas; o Postgres faz o resto (ILIKE)."""
    termo = (termo or "").strip()
    return "".join(
        c for c in unicodedata.normalize("NFD", termo) if unicodedata.category(c) != "Mn"
    )


_PALAVRAS_VAZIAS = {
    "de", "da", "do", "das", "dos", "para", "pra", "por", "com", "sem", "que", "uma", "um", "uns",
    "produto", "produtos", "bom", "boa", "bons", "boas", "melhor", "quero", "indica", "indique",
    "recomenda", "recomende", "algum", "alguma", "tem", "meu", "minha", "the", "and",
}


def palavras_de_busca(termo: str) -> list[str]:
    """Palavras relevantes (sem acento, minúsculas, >=3 letras, sem palavras
    vazias) para busca por qualquer-palavra. Sem sobrar nenhuma, devolve `[]`."""
    import re

    vistas: list[str] = []
    for w in re.findall(r"[a-z0-9]+", normalizar_termo(termo).lower()):
        if len(w) >= 3 and w not in _PALAVRAS_VAZIAS and w not in vistas:
            vistas.append(w)
    return vistas


def radical_de_busca(termo: str) -> str:
    """Radical grosseiro para casar português x INCI ('niacinamida' ->
    'niacinami' casa 'NIACINAMIDE'): tira as 2 últimas letras de termos de 1
    palavra com 7+ letras; senão devolve o próprio termo."""
    termo = normalizar_termo(termo).lower()
    return termo[:-2] if " " not in termo and len(termo) >= 7 else termo


def sem_acento(coluna: str) -> str:
    """Fragmento SQL que remove acentos de `coluna` (sem precisar de `unaccent`)."""
    return f"translate({coluna}, '{_COM_ACENTO}', '{_SEM_ACENTO}')"


def nao_encontrado(mensagem: str) -> dict:
    return {"encontrado": False, "mensagem": mensagem}


def erro_tool(nome: str, exc: Exception) -> dict:
    logger.exception("Falha na tool %s", nome)
    return {
        "erro": f"falha ao consultar o banco na tool {nome}",
        "detalhe": type(exc).__name__,
    }


async def consultar(pool: Any, nome: str, query: str, *args: Any, uma_linha: bool = False,
                    vazio: str = "nenhum resultado encontrado") -> Any:
    """Executa `query` com parâmetros ($1, $2...) e devolve:

    - `uma_linha=True`: o dict da linha, ou `{"encontrado": False, ...}`;
    - `uma_linha=False`: a lista de dicts, ou `{"encontrado": False, ...}`
      quando vazia (nunca `[]` — o especialista não pode confundir "não
      achei" com "não consultei");
    - em qualquer exceção do driver: `{"erro": ..., "detalhe": ...}`.
    """
    try:
        async with pool.acquire() as conn:
            if uma_linha:
                linha = await conn.fetchrow(query, *args)
                return _limpar(dict(linha)) if linha else nao_encontrado(vazio)
            linhas = await conn.fetch(query, *args)
    except Exception as exc:  # noqa: BLE001 — qualquer falha de driver vira resposta estruturada
        return erro_tool(nome, exc)
    if not linhas:
        return nao_encontrado(vazio)
    return [_limpar(dict(linha)) for linha in linhas]


async def executar(pool: Any, nome: str, query: str, *args: Any) -> Any:
    """Para escritas (INSERT/UPDATE/DELETE): devolve o status ou o erro."""
    try:
        async with pool.acquire() as conn:
            return await conn.execute(query, *args)
    except Exception as exc:  # noqa: BLE001
        return erro_tool(nome, exc)


def _limpar(linha: dict) -> dict:
    """Converte tipos não serializáveis em JSON (Decimal, datetime)."""
    from datetime import date, datetime
    from decimal import Decimal

    saida = {}
    for chave, valor in linha.items():
        if isinstance(valor, Decimal):
            valor = float(valor)
        elif isinstance(valor, (datetime, date)):
            valor = valor.isoformat()
        saida[chave] = valor
    return saida
