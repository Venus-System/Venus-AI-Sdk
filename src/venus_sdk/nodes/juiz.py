"""Nó Agente Juiz: valida a saída dos especialistas antes do orquestrador."""

from __future__ import annotations

import json
import logging
import re
from typing import Literal

from venus_sdk.llm.models import get_llm_juiz
from venus_sdk.nodes._amarracao_produtos import conferir_amarracao, mesmo_nome, nomes_por_id
from venus_sdk.nodes._evidencias import dados_da_evidencia
from venus_sdk.nodes._historico import historico_recente
from venus_sdk.prompts.comum import com_data_atual
from venus_sdk.prompts.juiz import JUIZ_PROMPT_COMPLETO
from venus_sdk.nodes.state import EstadoVenus
from venus_sdk.texto import remover_acentos
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


# Afirmações além de nome/nota (composição, alergia, risco, preferência): com
# qualquer uma delas, a resposta de produto ainda passa pelo Juiz LLM mesmo
# com a amarração nota <-> product_id conferida pelo código.
_RE_AFIRMA_ALEM_DA_NOTA = re.compile(
    r"cont[eé]m|\bconter\b|ingrediente|composi[cç]|f[oó]rmula|\blivre|alerg|rea[cç][aãõ]|irrit|[áa]lcool|fragr[aâ]ncia"
    r"|perfume|parabeno|sulfato|silicone|ceramida|niacinamida|[áa]cido|vitamina|retinol|glicer|hialur[oô]nico"
    r"|risco|contraindic|vegan|dermatolog|sintoma|\b(com|de)\s+(manteiga|[óo]leo|prote[íi]na|queratina|extrato)"
    # Adequação ao perfil ("adequado para pele oleosa") também é afirmação a auditar.
    r"|adequad|\bindicad|\bideal\b|\bperfil\b|\bpele\s+(oleosa|seca|mista|sens[íi]vel|normal|acneica)"
    r"|\bcabelo\s+(cachead|crespo|liso|ondulad|oleos|sec)|\btipo\s+de\s+(pele|cabelo)"
    # Avaliação sem número ("nota alta em segurança", "bem avaliado").
    r"|\bnotas?\s+(alta|baixa|boa|ruim|[óo]tima|excelente)|\bbem\s+avaliad|seguran[cç]a|\bsegur[oa]\b"
    # Benefício/textura, que nenhuma tool de produto devolve.
    r"|\bleve\b|regenera|sem\s+pesar|equilibrad|\bsuave|textura|absor[cç]|resseca",
    re.IGNORECASE,
)
# Na rotina, com os passos já conferidos contra `suggest_routine`, o que ainda
# precisa do Juiz LLM é afirmação de alergia/segurança. Enfeite de uso ("limpa
# sem ressecar") não: o orquestrador já reescreve nesse tom, e mandar ao LLM só
# trazia reprovação por "não repetiu 'só essa semana'" (chatbot real, 2026-10-21).
_RE_ROTINA_AFIRMA_SEGURANCA = re.compile(
    r"alerg|rea[cç][aãõ]|irrit|contraindic|dermatolog|sintoma|[áa]lcool|fragr[aâ]ncia|perfume|parabeno|sulfato"
    r"|risco|seguran[cç]a|\bsegur[oa]\b",
    re.IGNORECASE,
)
INTENCAO_CONFIRMAR = "confirmar"
_TOOLS_DA_ROTINA_SO_LEITURA = {"suggest_routine", "get_user_profile", "get_user_favorites", "get_user_lists", "get_user_allergies"}
INTENCAO_ERRO_FORMATO = "erro_formato"

# JSON inválido/cortado (ex.: rotina com um objeto "rotina" repetindo os passos,
# cortado no meio — chatbot real, 2026-10-21): não há conteúdo para o Juiz LLM
# auditar, só gastava uma chamada e devolvia um feedback vago.
_FEEDBACK_ERRO_FORMATO = (
    "Sua saída anterior não era um JSON válido (veio cortada ou com texto fora do JSON). "
    "Responda só com o JSON dos campos mínimos, curto, sem markdown e sem campos extras."
)
_FEEDBACK_SUGERIU_O_PROPRIO_PRODUTO = (
    "O pedido é de sugestão: não ofereça o próprio PRODUTO_EM_CONTEXTO como sugestão. "
    "Sugira outros produtos que search_product devolveu (outro product_id), ou diga que não achou alternativa."
)

# Campos das tools que não servem para auditar nada e só inflavam a entrada do
# Juiz (o Groq limita tokens/min e cada 429 empurrava a chamada para um modelo
# mais lento da cadeia).
_CAMPOS_SEM_VALOR_PARA_O_JUIZ = {"description", "slug", "relevancia"}


def _especialista_como_dict(estado: EstadoVenus) -> dict:
    """`resposta_especialista` se for um objeto; `{}` se veio lista/texto (JSON
    fora do contrato, ver `nodes/_json.py`)."""
    especialista = estado.get("resposta_especialista")
    return especialista if isinstance(especialista, dict) else {}


def _texto_do_especialista(especialista: dict, campos: tuple[str, ...] = ("resposta", "recomendacao")) -> str:
    return " ".join(str(especialista.get(campo) or "") for campo in campos)


def _confirmacao_curta(estado: EstadoVenus) -> bool:
    """Usuário só afirmou algo ("cadastrei os produtos que vc falou") e o
    especialista confirmou em uma frase, sem tool e sem dado: não há o que
    auditar. Na conversa real de 2026-10-20 o especialista respondia a uma
    afirmação com uma nova sugestão cheia de notas, e o Juiz reprovava."""
    especialista = _especialista_como_dict(estado)
    if especialista.get("intencao") != INTENCAO_CONFIRMAR or estado.get("evidencias_tools"):
        return False
    texto = _texto_do_especialista(especialista, ("resposta", "recomendacao", "acompanhamento"))
    return bool(texto.strip()) and not _RE_AFIRMA_DADO.search(texto)


def _so_nomes_e_notas_conferidos(especialista: dict) -> bool:
    """A amarração já foi conferida pelo código e o texto não afirma mais nada
    que o Juiz LLM precise auditar (composição, alergia, risco...)."""
    if especialista.get("alerta_seguranca") or especialista.get("encaminhar_profissional"):
        return False
    # "confirmar" que consultou produtos está afirmando algo sobre a conversa/conta
    # (chatbot real: "sua rotina tem os produtos 3 e 1", falso) — vai ao Juiz LLM.
    if especialista.get("intencao") in ("investigar_reacao", INTENCAO_CONFIRMAR):
        return False
    texto = _texto_do_especialista(especialista, ("resposta", "recomendacao", "acompanhamento", "esclarecer"))
    return not _RE_AFIRMA_ALEM_DA_NOTA.search(texto)


def _sugeriu_so_o_produto_em_contexto(estado: EstadoVenus) -> bool:
    """Pedido de sugestão ("sim, sugira") respondido só com o produto aberto na
    tela, embora a busca tenha achado outros (chatbot real, 2026-10-21: o
    especialista "sugeriu" o próprio CeraVe Creme Hidratante)."""
    especialista = _especialista_como_dict(estado)
    em_contexto = estado.get("produto_em_contexto")
    produtos = especialista.get("produtos")
    if especialista.get("intencao") != "sugerir" or not em_contexto or not isinstance(produtos, list) or not produtos:
        return False
    if not all(isinstance(item, dict) and mesmo_nome(str(item.get("nome") or ""), em_contexto) for item in produtos):
        return False
    encontrados = nomes_por_id(estado.get("evidencias_tools")).values()
    return any(nome and not mesmo_nome(nome, em_contexto) for nome in encontrados)


def _rotina_fiel_a_tool(estado: EstadoVenus) -> bool:
    """Rotina que só repete os passos de `suggest_routine` (todos citados, alerta
    de alergia quando houve exclusão) e não afirma mais nada a auditar. No
    chatbot real (2026-10-21) o Juiz LLM reprovava uma rotina idêntica à da
    tool por "não repetir 'só essa semana'" e por achar o creme de mãos pouco
    adequado — o mérito da escolha é da tool, não do Juiz."""
    especialista = _especialista_como_dict(estado)
    if estado.get("rota") != "rotina" and especialista.get("dominio") != "rotina":
        return False
    # Agenda, check-up e proposta de agendamento trazem afirmações que só o
    # Juiz LLM audita ("você está livre", "esses dois irritam juntos").
    evidencias = estado.get("evidencias_tools") or []
    if any(evidencia.get("tool") not in _TOOLS_DA_ROTINA_SO_LEITURA for evidencia in evidencias):
        return False
    rotinas = [
        dados_da_evidencia([evidencia], "suggest_routine")
        for evidencia in estado.get("evidencias_tools") or []
        if evidencia.get("tool") == "suggest_routine"
    ]
    if not rotinas or not all(isinstance(rotina, dict) and rotina.get("passos") for rotina in rotinas):
        return False
    if any(rotina.get("excluidos_por_alergia") for rotina in rotinas) and not especialista.get("alerta_alergia"):
        return False
    texto = _texto_do_especialista(especialista, ("resposta", "recomendacao", "acompanhamento", "esclarecer"))
    texto_normalizado = remover_acentos(texto).lower()
    citou_todos = all(
        _passo_citado(str(passo.get("nome") or ""), texto_normalizado)
        for rotina in rotinas for passo in rotina["passos"] if isinstance(passo, dict)
    )
    return citou_todos and not _RE_ROTINA_AFIRMA_SEGURANCA.search(texto)


def _passo_citado(nome: str, texto_normalizado: str) -> bool:
    """Nome completo do passo no texto, ou o nome sem a marca na frente
    ("Espuma Cremosa de Limpeza Hidratante" por "CeraVe Espuma Cremosa de
    Limpeza Hidratante") — ainda específico o bastante para um só produto."""
    nome = remover_acentos(nome).lower().strip()
    if not nome:
        return True
    sem_marca = nome.split(" ", 1)[1] if " " in nome else ""
    return nome in texto_normalizado or (len(sem_marca.split()) >= 2 and sem_marca in texto_normalizado)


def _evidencias_para_o_juiz(evidencias: list[dict]) -> list[dict]:
    """Cópia das evidências sem os campos que não servem para auditar."""
    compactas = []
    for evidencia in evidencias:
        resultado = dados = evidencia.get("resultado")
        try:
            # O retorno pode chegar serializado mais de uma vez (ver `_evidencias.py`).
            while isinstance(dados, str):
                dados = json.loads(dados)
        except ValueError:
            dados = None
        if isinstance(dados, (dict, list)):
            resultado = json.dumps(_sem_campos_inuteis(dados), ensure_ascii=False)
        compactas.append({**evidencia, "resultado": resultado})
    return compactas


def _sem_campos_inuteis(dados: object) -> object:
    if isinstance(dados, list):
        return [_sem_campos_inuteis(item) for item in dados]
    if isinstance(dados, dict):
        return {chave: valor for chave, valor in dados.items() if chave not in _CAMPOS_SEM_VALOR_PARA_O_JUIZ}
    return dados


def _feedback_da_amarracao(problemas: list[str]) -> str:
    return (
        "A amarração das notas aos produtos não bate com RESULTADOS_TOOLS: "
        + "; ".join(problemas)
        + ". Corrija só isso, mantendo os mesmos produtos e citando cada nota junto do produto dono dela."
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
def _pedido_de_esclarecimento_honesto(estado: EstadoVenus) -> bool:
    """Especialista só pediu esclarecimento (`esclarecer`), sem afirmar nota nem
    ingrediente: não há dado para auditar. Na conversa real de 2026-10-20 o Juiz
    LLM reprovou "qual vc recomendaria?" -> "qual seu tipo de pele e necessidade?"
    exigindo uma tool que nem existe, e o usuário recebeu uma resposta genérica."""
    especialista = estado.get("resposta_especialista")
    if not isinstance(especialista, dict):
        return False
    if not str(especialista.get("esclarecer") or "").strip():
        return False
    texto = " ".join(str(especialista.get(campo) or "") for campo in ("resposta", "recomendacao"))
    return not _RE_AFIRMA_DADO.search(texto)


def _veredito(aprovado: bool, feedback: str | None, tentativas: int) -> EstadoVenus:
    return {"aprovado_juiz": aprovado, "feedback_juiz": feedback, "tentativas_juiz": tentativas}


def _montar_entrada_juiz(estado: EstadoVenus, amarracao_verificada: str | None = None) -> str:
    entrada = f"PERGUNTA_ORIGINAL={estado.get('pergunta_original', '')}\n"
    # Mesmo contexto que o especialista recebeu (ver
    # `nodes/especialistas.py::_montar_entrada`): sem ele, "sim, sugira" parecia
    # não ter relação com a resposta e o Juiz reprovava pelo critério 1.
    if estado.get("produto_em_contexto"):
        entrada += f"PRODUTO_EM_CONTEXTO={estado['produto_em_contexto']}\n"
    historico = historico_recente(estado)
    if historico:
        entrada += f"HISTORICO_RECENTE (só contexto, nunca fonte de dado):\n{historico}\n"
    entrada += f"ESPECIALISTA_JSON={json.dumps(estado.get('resposta_especialista') or {}, ensure_ascii=False)}"
    # Evidência bruta das tools chamadas nesta tentativa (ver
    # `nodes/especialistas.py::_extrair_evidencias_tools`) — sem isto, o
    # Juiz só via o JSON final e não tinha como notar quando uma tool citada
    # em `fontes_usadas` não sustentava, de verdade, a afirmação feita
    # (achado de um teste de conversa real em 2026-09-10: produto sem
    # ingrediente cadastrado, resposta "inventou" ingredientes, aprovado).
    evidencias = estado.get("evidencias_tools")
    if evidencias:
        entrada += f"\nRESULTADOS_TOOLS={json.dumps(_evidencias_para_o_juiz(evidencias), ensure_ascii=False)}"
    if amarracao_verificada:
        entrada += f"\nAMARRACAO_VERIFICADA={amarracao_verificada}"
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
    if especialista.get("intencao") == INTENCAO_ERRO_FORMATO:
        return _veredito(False, _FEEDBACK_ERRO_FORMATO, proxima_tentativa)
    # Antes das aprovações rápidas: uma sugestão "honesta" do próprio produto em
    # tela ainda não responde ao pedido.
    if _sugeriu_so_o_produto_em_contexto(estado):
        return _veredito(False, _FEEDBACK_SUGERIU_O_PROPRIO_PRODUTO, proxima_tentativa)
    if (
        _resposta_honesta_sem_dado(estado)
        or _pedido_de_esclarecimento_honesto(estado)
        or _confirmacao_curta(estado)
        or _rotina_fiel_a_tool(estado)
    ):
        return _veredito(True, None, proxima_tentativa)
    feedback_do_calculo = _numero_calculado_ausente(estado)
    if feedback_do_calculo:
        logger.info("Juiz: número diferente do calculado pela tool; reprovado sem LLM")
        return _veredito(False, feedback_do_calculo, proxima_tentativa)

    amarracao_verificada = None
    if estado.get("rota") == "produto" or especialista.get("dominio") == "produto":
        # Nota <-> product_id conferida pelo código (ver `nodes/_amarracao_produtos.py`):
        # o erro vira um feedback exato, e o Juiz LLM não reconfere números.
        problemas, amarracao_verificada = conferir_amarracao(especialista, estado.get("evidencias_tools"))
        if problemas:
            logger.info("Juiz reprovou a amarração nota/produto sem LLM: %s", problemas)
            return _veredito(False, _feedback_da_amarracao(problemas), proxima_tentativa)
        if amarracao_verificada and _so_nomes_e_notas_conferidos(especialista):
            return _veredito(True, None, proxima_tentativa)

    entrada = _montar_entrada_juiz(estado, amarracao_verificada)
    mensagens = [("system", com_data_atual(JUIZ_PROMPT_COMPLETO)), ("human", entrada)]
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
