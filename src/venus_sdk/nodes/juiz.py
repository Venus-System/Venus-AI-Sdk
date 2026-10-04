"""Nó Agente Juiz: valida a saída dos especialistas antes do orquestrador."""

from __future__ import annotations

import json
import logging
import re
from typing import Literal

from venus_sdk.llm.models import get_llm_juiz
from venus_sdk.nodes._evidencias import dados_da_evidencia
from venus_sdk.prompts.comum import com_data_atual
from venus_sdk.prompts.juiz import JUIZ_PROMPT_COMPLETO
from venus_sdk.state import EstadoVenus
from venus_sdk.tools.calculos import TOOLS_COM_NUMERO_A_CITAR, formatar_numero

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
# novo antes de seguir mesmo assim para o orquestrador.
MAX_TENTATIVAS_JUIZ = 2

# `\[?...\]?` tolera o LLM ecoar o formato `RESULTADO=[aprovado|reprovado]`
# do próprio protocolo do prompt (`prompts/juiz.py`) ao pé da letra — sem
# isso, `RESULTADO=[aprovado]` não casava (`\w+` não inclui `[`) e o Juiz
# tratava como reprovado mesmo quando o LLM quis dizer "aprovado".
_RESULTADO_RE = re.compile(r"RESULTADO=\[?(\w+)\]?", re.IGNORECASE)
_FEEDBACK_RE = re.compile(r"FEEDBACK=\[?(.*?)\]?\s*$", re.IGNORECASE | re.DOTALL)

# Trechos que afirmam um dado do catálogo (nota, %, composição).
_RE_AFIRMA_DADO = re.compile(
    r"\d+\s*/\s*100|\d\s*%|\bscore\b|\bnota\s*\d|\bcont[eé]m\b|\blivre\b|\bsem\s+(sulfato|parabeno|silicone|[áa]lcool)"
    r"|\b(com|de)\s+(manteiga|[óo]leo|prote[íi]na|queratina|[áa]cido|vitamina|extrato)",
    re.IGNORECASE,
)


def _resposta_honesta_sem_dado(estado: EstadoVenus) -> bool:
    """Sugestão de produtos que só lista o que a busca achou, sem afirmar nota/ingrediente:
    não há o que reprovar — o Juiz LLM só pediria dado que o banco não tem."""
    especialista = estado.get("resposta_especialista")
    if not isinstance(especialista, dict) or especialista.get("dominio") != "produto" or especialista.get("intencao") != "sugerir":
        return False
    evidencias = estado.get("evidencias_tools") or []
    if not any(isinstance(ev, dict) and ev.get("tool") == "search_product" for ev in evidencias):
        return False
    texto = " ".join(str(especialista.get(campo) or "") for campo in ("resposta", "recomendacao", "esclarecer"))
    return bool(texto.strip()) and not _RE_AFIRMA_DADO.search(texto)


_NUMERO_NO_TEXTO = re.compile(r"\d[\d.,]*\d|\d")
_TOLERANCIA = 1e-6


def _leituras(numero: str) -> set[float]:
    """Os valores que um número escrito pode ter: "20,0" e "20.000" são
    ambíguos entre os formatos brasileiro e americano, então vale qualquer
    leitura (a checagem só reprova quando NENHUM número do texto bate)."""
    leituras = set()
    for decimal, milhar in ((",", "."), (".", ",")):
        try:
            leituras.add(float(numero.replace(milhar, "").replace(decimal, ".")))
        except ValueError:
            pass
    return leituras


def _numero_calculado_ausente(estado: EstadoVenus) -> str | None:
    """Feedback de reprovação quando uma tool de cálculo rodou e a resposta
    não traz o número que ela devolveu (ou seja, o LLM fez outra conta). A
    checagem é determinística: não depende do LLM do Juiz achar o erro."""
    especialista = estado.get("resposta_especialista")
    if not isinstance(especialista, dict):
        return None
    texto = " ".join(str(especialista.get(campo) or "") for campo in ("resposta", "recomendacao"))
    numeros_no_texto = set().union(*(_leituras(n) for n in _NUMERO_NO_TEXTO.findall(texto))) or set()
    evidencias = estado.get("evidencias_tools") or []
    for nome in {e.get("tool") for e in evidencias if isinstance(e, dict)} & TOOLS_COM_NUMERO_A_CITAR:
        dados = dados_da_evidencia(evidencias, nome)
        if not isinstance(dados, dict) or not isinstance(dados.get("resultado"), (int, float)):
            continue
        esperado = float(dados["resultado"])
        if not any(abs(lido - esperado) <= _TOLERANCIA * max(1.0, abs(esperado)) for lido in numeros_no_texto):
            citar = dados.get("valor_para_citar") or formatar_numero(esperado)
            return (f"A resposta não usa o número calculado pela tool {nome}: {citar}. "
                    "Use exatamente o valor devolvido pela tool; nunca calcule de cabeça.")
    return None


def _veredito(aprovado: bool, feedback: str | None, tentativas: int) -> EstadoVenus:
    return {"aprovado_juiz": aprovado, "feedback_juiz": feedback, "tentativas_juiz": tentativas}


def _montar_entrada_juiz(estado: EstadoVenus) -> str:
    entrada = (
        f"PERGUNTA_ORIGINAL={estado.get('pergunta_original', '')}\n"
        f"ESPECIALISTA_JSON={json.dumps(estado.get('resposta_especialista') or {}, ensure_ascii=False)}"
    )
    evidencias = estado.get("evidencias_tools")
    if evidencias:
        entrada += f"\nRESULTADOS_TOOLS={json.dumps(evidencias, ensure_ascii=False)}"
    return entrada


def _ler_veredito_do_llm(texto: str) -> tuple[bool, str | None]:
    """`(aprovado, feedback)` a partir do protocolo `RESULTADO=`/`FEEDBACK=`.
    Sem `FEEDBACK=`, o texto inteiro vira o feedback da reprovação."""
    match_resultado = _RESULTADO_RE.search(texto)
    aprovado = bool(match_resultado) and match_resultado.group(1).strip().lower() == "aprovado"
    if aprovado:
        return True, None
    match_feedback = _FEEDBACK_RE.search(texto)
    return False, match_feedback.group(1).strip() if match_feedback else texto


def no_agente_juiz(estado: EstadoVenus) -> EstadoVenus:
    """Avalia `resposta_especialista` e atualiza `aprovado_juiz`,
    `feedback_juiz` e `tentativas_juiz`."""
    especialista = estado.get("resposta_especialista")
    if not isinstance(especialista, dict):
        especialista = {}
    if especialista.get("intencao") == "erro_tecnico":
        # O especialista falhou por infraestrutura (cota/rede/LLM fora do ar),
        # não por conteúdo ruim: reprovar e mandar tentar de novo só repetiria
        # a mesma falha (e, com cota estourada, gastaria mais tempo e mais
        # cota). Pula o LLM do juiz e vai direto para "esgotado" — o
        # orquestrador comunica o problema com transparência.
        logger.warning("Especialista %s falhou tecnicamente; sem retry do juiz", especialista.get("dominio"))
        return _veredito(False, None, MAX_TENTATIVAS_JUIZ)

    proxima_tentativa = estado.get("tentativas_juiz", 0) + 1
    if especialista.get("intencao") in ("nao_suportado", "agendamento"):
        # Recusa fixa escrita no código (ex.: a IA não salva favoritos) — não
        # há dado para auditar.
        return _veredito(True, None, proxima_tentativa)
    if _resposta_honesta_sem_dado(estado):
        return _veredito(True, None, proxima_tentativa)
    feedback_do_calculo = _numero_calculado_ausente(estado)
    if feedback_do_calculo:
        logger.info("Juiz: número diferente do calculado pela tool; reprovado sem LLM")
        return _veredito(False, feedback_do_calculo, proxima_tentativa)

    mensagens = [("system", com_data_atual(JUIZ_PROMPT_COMPLETO)), ("human", _montar_entrada_juiz(estado))]
    try:
        resposta = get_llm_juiz().invoke(mensagens)
    except Exception:
        # LLM do Juiz fora do ar: reprovação sem feedback, que segue o fluxo
        # normal de retry/"esgotado" em vez de derrubar a conversa.
        logger.exception("Falha ao chamar o LLM do Agente Juiz")
        return _veredito(False, None, proxima_tentativa)

    aprovado, feedback = _ler_veredito_do_llm((resposta.content or "").strip())
    return _veredito(aprovado, feedback, proxima_tentativa)


def decidir_pos_juiz(estado: EstadoVenus) -> ResultadoJuiz:
    """Aresta condicional pós-juiz: "aprovado"; "reprovado_<rota>" enquanto
    houver tentativas (volta direto ao mesmo especialista, com o feedback,
    sem re-rotear); ou "esgotado" (segue ao orquestrador sem aprovação)."""
    if estado.get("aprovado_juiz"):
        return "aprovado"
    if estado.get("tentativas_juiz", 0) >= MAX_TENTATIVAS_JUIZ:
        return "esgotado"
    return f"reprovado_{estado.get('rota')}"  # type: ignore[return-value]
