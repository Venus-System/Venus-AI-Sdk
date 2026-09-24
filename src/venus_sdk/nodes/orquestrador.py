"""Nó Orquestrador: transforma o JSON do especialista na resposta final."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from venus_sdk.llm.models import extrair_texto_resposta, get_llm_especialista
from venus_sdk.nodes._evidencias import dados_da_evidencia
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

_INTENCOES_DE_ERRO = {"erro_tecnico", "erro_formato"}
_DOMINIOS_COM_DADO_NO_BANCO = {"produto", "ingrediente", "rotina"}
_SEGURA_GENERICA = (
    "Não consegui confirmar essa informação com segurança. Pode me dizer o nome do produto ou "
    "ingrediente, ou perguntar de outro jeito?"
)
_AVISO_SEM_NOTA_NEM_INGREDIENTES = (
    "Esses ainda não têm nota nem ingredientes cadastrados aqui, então não consigo dizer qual "
    "é o melhor sem chutar. Se você me contar seu tipo de cacho/fio e o que quer (hidratar, definir, "
    "reduzir frizz), eu te ajudo a escolher entre eles — ou posso ver algum em particular."
)
_CONVITE_PARA_DETALHAR = "Quer que eu detalhe algum deles?"

# Quantos itens de cada busca entram na resposta montada sem o LLM.
_MAX_PRODUTOS_LISTADOS = 6
_MAX_INGREDIENTES_LISTADOS = 5

# Texto mais curto que isso não pode ser a tradução de uma resposta de verdade.
_TAMANHO_MINIMO_TEXTO_FINAL = 12

_PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{2,40}\]")
_NOME_DO_PASSO_RE = re.compile(r"\d+\) (.+?) \(")
_MARCA_PASSOS_DA_ROTINA = "Passos ("


# --- validação do texto do LLM ---


def _resposta_direta_do_json(dados: dict) -> str:
    """Formata a resposta SEM LLM, no mesmo formato do prompt (rede de segurança para modelos
    pequenos que devolvem saudação/placeholder em vez do conteúdo)."""
    partes = [f"- {str(dados.get('resposta') or '').strip()}"]
    if dados.get("recomendacao"):
        partes.append(f"- *Recomendação*: {str(dados['recomendacao']).strip()}")
    acompanhamento = dados.get("esclarecer") or dados.get("acompanhamento")
    if acompanhamento:
        partes.append(f"- *Acompanhamento*: {str(acompanhamento).strip()}")
    return "\n".join(partes)


def _texto_final_valido(texto: str, dados: dict) -> bool:
    """Falso se o LLM devolveu placeholder ([nome]) ou ignorou o conteúdo do especialista."""
    if not texto or _PLACEHOLDER_RE.search(texto):
        return False
    resposta = str(dados.get("resposta") or "").strip()
    if resposta and len(texto) < _TAMANHO_MINIMO_TEXTO_FINAL:
        return False
    # Passos de rotina anexados pelo especialista ("1) Nome (Categoria); ...") não podem se perder.
    if _MARCA_PASSOS_DA_ROTINA in resposta:
        trecho_dos_passos = resposta.split(_MARCA_PASSOS_DA_ROTINA, 1)[1]
        nomes = _NOME_DO_PASSO_RE.findall(trecho_dos_passos)
        if nomes and not any(nome.lower() in texto.lower() for nome in nomes):
            return False
    return True


# --- resposta montada só com a evidência das tools ---


def _parte_rotina(evidencias: list[dict] | None) -> str | None:
    dados = dados_da_evidencia(evidencias, "suggest_routine")
    passos = dados.get("passos") if isinstance(dados, dict) else None
    if not passos:
        return None
    lista = "; ".join(f"{passo['ordem']}) {passo['nome']}" for passo in passos)
    return f"Montei sua rotina com o que você já tem salvo: {lista}."


def _parte_produtos(evidencias: list[dict] | None) -> tuple[str | None, bool]:
    """`(texto, sem_dado)` — `sem_dado` indica que nenhum produto listado tem
    nota nem ingredientes cadastrados."""
    produtos = dados_da_evidencia(evidencias, "search_product")
    if not isinstance(produtos, list) or not produtos:
        return None, False
    itens = [p for p in produtos[:_MAX_PRODUTOS_LISTADOS] if isinstance(p, dict) and "name" in p]
    if not itens:
        return None, False
    linhas = "\n".join(f"- {p['name']} ({p['brand_name']})" for p in itens)
    sem_dado = not any(p.get("tem_score") or p.get("tem_ingredientes") for p in itens)
    return f"Olha o que encontrei no nosso catálogo:\n\n{linhas}", sem_dado


def _parte_ingredientes(evidencias: list[dict] | None) -> str | None:
    ingredientes = dados_da_evidencia(evidencias, "search_ingredient")
    if not isinstance(ingredientes, list) or not ingredientes:
        return None
    nomes = ", ".join(
        str(i.get("common_name")) for i in ingredientes[:_MAX_INGREDIENTES_LISTADOS] if isinstance(i, dict)
    )
    return f"Encontrei no catálogo: {nomes}." if nomes else None


def _parte_alergias(evidencias: list[dict] | None) -> str | None:
    alergias = dados_da_evidencia(evidencias, "get_user_allergies")
    if not isinstance(alergias, list) or not alergias:
        return None
    nomes = ", ".join(str(a.get("allergy_name")) for a in alergias if isinstance(a, dict))
    return f"Você declarou alergia a: {nomes}." if nomes else None


def _resposta_segura_sem_aprovacao(estado: EstadoVenus) -> str:
    """Quando o Juiz esgota as tentativas em produto/ingrediente/rotina, o texto do especialista
    (reprovado por inventar dado) NÃO chega ao usuário. Monta uma resposta só com o que as
    tools realmente devolveram."""
    evidencias = estado.get("evidencias_tools")
    texto_produtos, sem_dado = _parte_produtos(evidencias)
    candidatas = [
        _parte_rotina(evidencias),
        texto_produtos,
        _parte_ingredientes(evidencias),
        _parte_alergias(evidencias),
    ]
    partes = [parte for parte in candidatas if parte]

    if not partes:
        return _SEGURA_GENERICA
    partes.append(_AVISO_SEM_NOTA_NEM_INGREDIENTES if sem_dado else _CONVITE_PARA_DETALHAR)
    return "\n\n".join(partes)


# --- nó ---


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


def _montar_entrada_orquestrador(especialista_json: Any, aprovado: bool) -> str:
    entrada = f"ESPECIALISTA_JSON={json.dumps(especialista_json, ensure_ascii=False)}"
    # Chegou aqui via "esgotado" (ver `nodes/juiz.py`) sem aprovação plena.
    if not aprovado:
        entrada += "\n\n" + _NOTA_JUIZ_ESGOTADO
    return entrada


def no_orquestrador(estado: EstadoVenus) -> EstadoVenus:
    """Chama o LLM orquestrador com `ORQUESTRADOR_PROMPT_COMPLETO` e
    `resposta_especialista`, gravando o texto final em `resposta_final`."""
    especialista_json = estado.get("resposta_especialista") or {}
    especialista_e_objeto = isinstance(especialista_json, dict)
    aprovado = estado.get("aprovado_juiz", True)

    # Falha técnica/de formato do especialista: nada útil pra "traduzir" — mensagem simples ao
    # usuário, sem passar pelo LLM (e sem expor erro nenhum).
    if especialista_e_objeto and especialista_json.get("intencao") in _INTENCOES_DE_ERRO:
        return {"resposta_final": _RESPOSTA_ORQUESTRADOR_FALLBACK}
    # Juiz reprovou (tentativas esgotadas) em domínio com dado no banco: nunca repassa o texto
    # reprovado (ele costuma conter invenção); usa só a evidência das tools.
    if not aprovado and especialista_e_objeto and especialista_json.get("dominio") in _DOMINIOS_COM_DADO_NO_BANCO:
        return {"resposta_final": _resposta_segura_sem_aprovacao(estado)}

    entrada = _montar_entrada_orquestrador(especialista_json, aprovado)
    mensagens = [("system", ORQUESTRADOR_PROMPT_COMPLETO), ("human", entrada)]
    texto = _invocar_orquestrador(mensagens)
    if not texto:
        # Falha pontual do LLM (conteúdo vazio, ou exceção — ver
        # `_invocar_orquestrador`); tenta mais uma vez antes de cair no
        # fallback fixo.
        texto = _invocar_orquestrador(mensagens)

    if (
        especialista_e_objeto
        and especialista_json.get("resposta")
        and not _texto_final_valido(texto, especialista_json)
    ):
        logger.warning("Orquestrador devolveu texto inválido (placeholder/saudação); usando o conteúdo do especialista")
        texto = _resposta_direta_do_json(especialista_json)

    return {"resposta_final": texto or _RESPOSTA_ORQUESTRADOR_FALLBACK}
