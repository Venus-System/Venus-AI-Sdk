"""Nó Agente Juiz: valida a saída dos especialistas antes do orquestrador."""

from __future__ import annotations

import json
import logging
import re
from typing import Literal

from venus_sdk.llm.models import get_llm_rapido
from venus_sdk.prompts.juiz import JUIZ_PROMPT_COMPLETO
from venus_sdk.state import EstadoVenus

logger = logging.getLogger(__name__)

ResultadoJuiz = Literal[
    "aprovado",
    "reprovado_produto",
    "reprovado_ingrediente",
    "reprovado_rotina",
    "reprovado_faq",
    "esgotado",
]

# Nº máximo de vezes que o Agente Juiz pode mandar o especialista tentar de
# novo (via roteador) antes de seguir mesmo assim para o orquestrador.
MAX_TENTATIVAS_JUIZ = 2

# `\[?...\]?` tolera o LLM ecoar o formato `RESULTADO=[aprovado|reprovado]`
# do próprio protocolo do prompt (`prompts/juiz.py`) ao pé da letra — sem
# isso, `RESULTADO=[aprovado]` não casava (`\w+` não inclui `[`) e o Juiz
# tratava como reprovado mesmo quando o LLM quis dizer "aprovado".
_RESULTADO_RE = re.compile(r"RESULTADO=\[?(\w+)\]?", re.IGNORECASE)
_FEEDBACK_RE = re.compile(r"FEEDBACK=\[?(.*?)\]?\s*$", re.IGNORECASE | re.DOTALL)


_RE_AFIRMA_DADO = re.compile(
    r"\d+\s*/\s*100|\d\s*%|\bscore\b|\bnota\s*\d|\bcont[eé]m\b|\blivre\b|\bsem\s+(sulfato|parabeno|silicone|[áa]lcool)"
    r"|\b(com|de)\s+(manteiga|[óo]leo|prote[íi]na|queratina|[áa]cido|vitamina|extrato)",
    re.IGNORECASE,
)


def _resposta_honesta_sem_dado(estado: EstadoVenus) -> bool:
    """Sugestão de produtos que só lista o que a busca achou, sem afirmar nota/ingrediente:
    não há o que reprovar — o Juiz LLM só pediria dado que o banco não tem."""
    esp = estado.get("resposta_especialista") or {}
    if esp.get("dominio") != "produto" or esp.get("intencao") != "sugerir":
        return False
    ev = estado.get("evidencias_tools") or []
    if not any(isinstance(e, dict) and e.get("tool") == "search_product" for e in ev):
        return False
    texto = " ".join(str(esp.get(k) or "") for k in ("resposta", "recomendacao", "esclarecer"))
    return bool(texto.strip()) and not _RE_AFIRMA_DADO.search(texto)


def no_agente_juiz(estado: EstadoVenus) -> EstadoVenus:
    """Avalia `resposta_especialista` e atualiza `aprovado_juiz`,
    `feedback_juiz` e `tentativas_juiz`."""
    especialista = estado.get("resposta_especialista") or {}
    if especialista.get("intencao") == "erro_tecnico":
        # O especialista falhou por infraestrutura (cota/rede/LLM fora do ar),
        # não por conteúdo ruim: reprovar e mandar tentar de novo só repetiria
        # a mesma falha (e, com cota estourada, gastaria mais tempo e mais
        # cota). Pula o LLM do juiz e vai direto para "esgotado" — o
        # orquestrador comunica o problema com transparência.
        logger.warning("Especialista %s falhou tecnicamente; sem retry do juiz", especialista.get("dominio"))
        return {"aprovado_juiz": False, "feedback_juiz": None, "tentativas_juiz": MAX_TENTATIVAS_JUIZ}

    if _resposta_honesta_sem_dado(estado):
        return {"aprovado_juiz": True, "feedback_juiz": None,
                "tentativas_juiz": estado.get("tentativas_juiz", 0) + 1}

    entrada = (
        f"PERGUNTA_ORIGINAL={estado.get('pergunta_original', '')}\n"
        f"ESPECIALISTA_JSON={json.dumps(estado.get('resposta_especialista') or {}, ensure_ascii=False)}"
    )
    # Evidência bruta das tools chamadas nesta tentativa (ver
    # `nodes/especialistas.py::_extrair_evidencias_tools`) — sem isto, o
    # Juiz só via o JSON final e não tinha como notar quando uma tool citada
    # em `fontes_usadas` não sustentava, de verdade, a afirmação feita
    # (achado de um teste de conversa real em 2026-09-10: produto sem
    # ingrediente cadastrado, resposta "inventou" ingredientes, aprovado).
    evidencias = estado.get("evidencias_tools")
    if evidencias:
        entrada += f"\nRESULTADOS_TOOLS={json.dumps(evidencias, ensure_ascii=False)}"
    mensagens = [("system", JUIZ_PROMPT_COMPLETO), ("human", entrada)]

    try:
        resposta = get_llm_rapido().invoke(mensagens)
    except Exception:
        # LLM do Juiz indisponível — não deixa isso subir cru até o
        # `.ainvoke()` do grafo principal. Trata como reprovação silenciosa
        # (sem feedback específico pro especialista tentar de novo): reusa o
        # fluxo normal de retry/`esgotado` em `decidir_pos_juiz`, que depois
        # de `MAX_TENTATIVAS_JUIZ` segue pro orquestrador com a nota de
        # transparência de sempre — em vez de travar a conversa inteira.
        logger.exception("Falha ao chamar o LLM do Agente Juiz")
        tentativas = estado.get("tentativas_juiz", 0) + 1
        return {"aprovado_juiz": False, "feedback_juiz": None, "tentativas_juiz": tentativas}
    texto = (resposta.content or "").strip()

    match_resultado = _RESULTADO_RE.search(texto)
    aprovado = bool(match_resultado) and match_resultado.group(1).strip().lower() == "aprovado"

    feedback = None
    if not aprovado:
        match_feedback = _FEEDBACK_RE.search(texto)
        feedback = match_feedback.group(1).strip() if match_feedback else texto

    return {
        "aprovado_juiz": aprovado,
        "feedback_juiz": feedback,
        "tentativas_juiz": estado.get("tentativas_juiz", 0) + 1,
    }


def decidir_pos_juiz(estado: EstadoVenus) -> ResultadoJuiz:
    """Aresta condicional pós-juiz.

    Devolve "reprovado_<dominio>" (produto/ingrediente/rotina/faq) enquanto
    houver tentativas disponíveis — volta DIRETO pro nó do especialista que
    gerou a resposta (ver `flows/venus_flow.py`), não mais pro roteador:
    reprovar não muda a intenção/rota já classificada, só pede pro mesmo
    especialista tentar de novo com o feedback do Juiz (já lido de
    `feedback_juiz` em `nodes/especialistas.py::_montar_entrada`,
    independente de por onde se chega até ele). Antes, isso gastava uma
    chamada de LLM a mais no roteador a cada retry e podia até re-rotear pra
    um especialista diferente do que gerou a resposta reprovada.

    Devolve "esgotado" quando as tentativas acabarem (segue para o
    orquestrador mesmo sem aprovação total).
    """
    if estado.get("aprovado_juiz"):
        return "aprovado"
    if estado.get("tentativas_juiz", 0) >= MAX_TENTATIVAS_JUIZ:
        return "esgotado"
    return f"reprovado_{estado.get('rota')}"  # type: ignore[return-value]
