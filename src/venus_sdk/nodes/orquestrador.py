"""Nó Orquestrador: transforma o JSON do especialista na resposta final."""

from __future__ import annotations

import json
from typing import Any
import re
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
    "Tive um probleminha pra montar a resposta agora. Pode perguntar de novo "
    "em instantes?"
)


_PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{2,40}\]")


def _resposta_direta_do_json(dados: dict) -> str:
    """Formata a resposta SEM LLM, no mesmo formato do prompt (rede de segurança para modelos
    pequenos que devolvem saudação/placeholder em vez do conteúdo)."""
    partes = [f"- {str(dados.get('resposta') or '').strip()}"]
    if dados.get("recomendacao"):
        partes.append(f"- *Recomendação*: {str(dados['recomendacao']).strip()}")
    extra = dados.get("esclarecer") or dados.get("acompanhamento")
    if extra:
        partes.append(f"- *Acompanhamento*: {str(extra).strip()}")
    return "\n".join(partes)


def _texto_final_valido(texto: str, dados: dict) -> bool:
    """Falso se o LLM devolveu placeholder ([nome]) ou ignorou o conteúdo do especialista."""
    if not texto or _PLACEHOLDER_RE.search(texto):
        return False
    resposta = str(dados.get("resposta") or "").strip()
    if resposta and len(texto) < 12:
        return False
    # Passos de rotina anexados pelo especialista ("1) Nome (Categoria); ...") não podem se perder.
    if "Passos (" in resposta:
        nomes = re.findall(r"\d+\) (.+?) \(", resposta.split("Passos (", 1)[1])
        if nomes and not any(n.lower() in texto.lower() for n in nomes):
            return False
    return True


_DOMINIOS_COM_DADO_NO_BANCO = {"produto", "ingrediente", "rotina"}
_SEGURA_GENERICA = (
    "Não consegui confirmar essa informação com segurança. Pode me dizer o nome do produto ou "
    "ingrediente, ou perguntar de outro jeito?"
)


def _dados_da_evidencia(evidencias: list[dict] | None, tool: str) -> Any:
    for ev in evidencias or []:
        if ev.get("tool") == tool:
            dados = ev.get("resultado")
            try:
                while isinstance(dados, str):
                    dados = json.loads(dados)
            except ValueError:
                return None
            return dados
    return None


def _resposta_segura_sem_aprovacao(estado: EstadoVenus) -> str:
    """Quando o Juiz esgota as tentativas em produto/ingrediente/rotina, o texto do especialista
    (reprovado por inventar dado) NÃO chega ao usuário. Monta uma resposta só com o que as
    tools realmente devolveram."""
    ev = estado.get("evidencias_tools")
    partes: list[str] = []

    passos = (_dados_da_evidencia(ev, "suggest_routine") or {}).get("passos") if isinstance(
        _dados_da_evidencia(ev, "suggest_routine"), dict) else None
    if passos:
        lista = "; ".join(f"{p['ordem']}) {p['nome']}" for p in passos)
        partes.append(f"Montei sua rotina com o que você já tem salvo: {lista}.")
    produtos = _dados_da_evidencia(ev, "search_product")
    sem_dado = False
    if isinstance(produtos, list) and produtos:
        itens = [p for p in produtos[:6] if isinstance(p, dict) and "name" in p]
        if itens:
            linhas = "\n".join(f"- {p['name']} ({p['brand_name']})" for p in itens)
            partes.append(f"Olha o que encontrei no nosso catálogo:\n\n{linhas}")
            sem_dado = not any(p.get("tem_score") or p.get("tem_ingredientes") for p in itens)
    ingredientes = _dados_da_evidencia(ev, "search_ingredient")
    if isinstance(ingredientes, list) and ingredientes:
        nomes = ", ".join(str(i.get("common_name")) for i in ingredientes[:5] if isinstance(i, dict))
        if nomes:
            partes.append(f"Encontrei no catálogo: {nomes}.")
    alergias = _dados_da_evidencia(ev, "get_user_allergies")
    if isinstance(alergias, list) and alergias:
        nomes = ", ".join(str(a.get("allergy_name")) for a in alergias if isinstance(a, dict))
        if nomes:
            partes.append(f"Você declarou alergia a: {nomes}.")

    if not partes:
        return _SEGURA_GENERICA
    if sem_dado:
        partes.append(
            "Esses ainda não têm nota nem ingredientes cadastrados aqui, então não consigo dizer qual "
            "é o melhor sem chutar. Se você me contar seu tipo de cacho/fio e o que quer (hidratar, definir, "
            "reduzir frizz), eu te ajudo a escolher entre eles — ou posso ver algum em particular."
        )
    else:
        partes.append("Quer que eu detalhe algum deles?")
    return "\n\n".join(partes)


def no_orquestrador(estado: EstadoVenus) -> EstadoVenus:
    """Chama o LLM orquestrador com `ORQUESTRADOR_PROMPT_COMPLETO` e
    `resposta_especialista`, gravando o texto final em `resposta_final`."""
    especialista_json = estado.get("resposta_especialista") or {}
    # Falha técnica/de formato do especialista: nada útil pra "traduzir" — mensagem simples ao
    # usuário, sem passar pelo LLM (e sem expor erro nenhum).
    if isinstance(especialista_json, dict) and especialista_json.get("intencao") in {"erro_tecnico", "erro_formato"}:
        return {"resposta_final": _RESPOSTA_ORQUESTRADOR_FALLBACK}
    # Juiz reprovou (tentativas esgotadas) em domínio com dado no banco: nunca repassa o texto
    # reprovado (ele costuma conter invenção); usa só a evidência das tools.
    if (not estado.get("aprovado_juiz", True) and isinstance(especialista_json, dict)
            and especialista_json.get("dominio") in _DOMINIOS_COM_DADO_NO_BANCO):
        return {"resposta_final": _resposta_segura_sem_aprovacao(estado)}
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

    if isinstance(especialista_json, dict) and especialista_json.get("resposta") and not _texto_final_valido(texto, especialista_json):
        logger.warning("Orquestrador devolveu texto inválido (placeholder/saudação); usando o conteúdo do especialista")
        texto = _resposta_direta_do_json(especialista_json)

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
