"""Nó Orquestrador: transforma o JSON do especialista na resposta final."""

from __future__ import annotations

import json
import logging

from venus_sdk.llm.models import extrair_texto_resposta, get_llm_especialista
from venus_sdk.prompts.orquestrador import ORQUESTRADOR_PROMPT_COMPLETO
from venus_sdk.state import EstadoVenus

logger = logging.getLogger(__name__)

_NOTA_JUIZ_ESGOTADO = (
    "NOTA_DO_SISTEMA=O Agente Juiz não conseguiu validar totalmente esta "
    "resposta após as tentativas disponíveis. Comunique isso ao usuário com "
    "transparência, sem alarmismo."
)

# Fallback para quando o LLM devolve conteúdo vazio mesmo após o retry (falha
# pontual do modelo) — evita cair na mensagem genérica de saída bloqueada
# depois de já termos passado por especialista + Agente Juiz de verdade.
_RESPOSTA_ORQUESTRADOR_FALLBACK = (
    "Consegui analisar sua pergunta, mas tive um problema pra formular a "
    "resposta agora — pode perguntar de novo?"
)


def no_orquestrador(estado: EstadoVenus) -> EstadoVenus:
    """Chama o LLM orquestrador com `ORQUESTRADOR_PROMPT_COMPLETO` e
    `resposta_especialista`, gravando o texto final em `resposta_final`."""
    especialista_json = estado.get("resposta_especialista") or {}
    entrada = f"ESPECIALISTA_JSON={json.dumps(especialista_json, ensure_ascii=False)}"

    # Chegou aqui via "esgotado" (ver `nodes/juiz.py`) sem aprovação plena.
    if not estado.get("aprovado_juiz", True):
        entrada += "\n\n" + _NOTA_JUIZ_ESGOTADO

    mensagens = [("system", ORQUESTRADOR_PROMPT_COMPLETO), ("human", entrada)]
    texto = _invocar_orquestrador(mensagens)
    if not texto:
        # Falha pontual do LLM (conteúdo vazio, ou exceção — ver
        # `_invocar_orquestrador`); tenta mais uma vez antes de cair no
        # fallback fixo.
        texto = _invocar_orquestrador(mensagens)

    return {"resposta_final": texto or _RESPOSTA_ORQUESTRADOR_FALLBACK}


def _invocar_orquestrador(mensagens: list) -> str:
    """`get_llm_especialista().invoke()` protegido contra exceção — sem
    isto, uma falha total de provedor (Gemini E o fallback Groq
    indisponíveis) subia crua até o `.ainvoke()` do grafo principal em vez
    de cair no fallback fixo, mesmo já existindo tratamento pro caso mais
    ameno de conteúdo vazio logo abaixo."""
    try:
        return extrair_texto_resposta(get_llm_especialista().invoke(mensagens)).strip()
    except Exception:
        logger.exception("Falha ao chamar o LLM do Orquestrador")
        return ""
