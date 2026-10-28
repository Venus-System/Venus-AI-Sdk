"""Nó Roteador: classifica a intenção e decide o especialista."""

from __future__ import annotations

import json
import logging
import re
from typing import Literal

from venus_sdk.llm.models import get_llm_rapido
from venus_sdk.nodes.agendamento import eh_confirmacao, eh_negacao
from venus_sdk.prompts.comum import com_data_atual
from venus_sdk.prompts.router import ROUTER_PROMPT_COMPLETO
from venus_sdk.nodes.state import EstadoVenus

logger = logging.getLogger(__name__)

# ROUTE= e PERGUNTA_ORIGINAL= são emitidos pelo LLM só quando o caso é de
# especialista; small talk/fora de escopo respondem em texto livre (ver
# `prompts/router.py`), daí a ausência de ROUTE= ser o sinal de resposta direta.
_ROUTE_RE = re.compile(r"ROUTE=(\w+)", re.IGNORECASE)

_ROTAS_VALIDAS: frozenset[str] = frozenset({"produto", "ingrediente", "rotina", "faq"})

# Na reação a ofensa (ver `prompts/router.py`), o roteador começa a resposta
# com a linha `REACAO=magoada`. A linha sai sempre do texto, com qualquer
# valor, e quem escolhe a expressão é o código: só `magoada` numa resposta
# direta mantida como veio vira `magoada`; o resto é `neutra`.
_REACAO_RE = re.compile(r"^[ \t]*REACAO[ \t]*=[ \t]*(\w*)[ \t]*(?:\n|$)", re.IGNORECASE | re.MULTILINE)

# Marcador que o LLM deixa no lugar do nome ("desculpa mesmo, [nome]!!"): sai
# junto com a vírgula de antes. A resposta direta não passa pelo orquestrador,
# que barra o mesmo caso. `[FAQ](https://...)` é link e fica.
_MARCADOR_RE = re.compile(r",?[ \t]*\[[^\]\n]{2,40}\](?!\()")

DecisaoRoteador = Literal["produto", "ingrediente", "rotina", "faq", "direto"]

# Fallback para quando o roteador não emite ROUTE= (small talk/fora de
# escopo) e, mesmo assim, o LLM devolve conteúdo vazio (falha pontual do
# modelo). Evita cair na mensagem genérica de saída bloqueada por algo tão
# simples quanto uma saudação.
_RESPOSTA_DIRETA_FALLBACK = (
    "Essa pergunta está fora do meu assunto, mas posso te ajudar com produto, "
    "ingrediente ou rotina de skincare e haircare — quer falar sobre algum deles?"
)

# Chamadas ao LLM roteador por invocação (a 1ª + uma nova tentativa se ela levantar).
_TENTATIVAS_LLM_ROTEADOR = 2

# Rede de segurança contra o roteador (LLM) rotear small talk para um
# especialista (achado ao vivo: "oi" caindo em ROUTE=rotina, ver
# `_recuperar_de_tool_call_alucinada`). Só cobre mensagens INTEIRAMENTE
# compostas de saudação/agradecimento/despedida — qualquer pedido real passa.
_SMALL_TALK_RE = re.compile(
    r"^\s*(oi+e?|ol[aá]+|opa|e a[ií]|bom dia|boa tarde|boa noite|hey|hello|hi|"
    r"tudo bem\??|como vai\??|obrigad[oa]|valeu|vlw|tchau|at[eé] mais|blz|beleza|ok)"
    r"(\s*[,!.?]*\s*(tudo bem\??|tudo bom\??|venus|v[eê]nus|td bem\??|"
    r"como vai\??|e voc[eê]\??))*\s*[!.?]*\s*$",
    re.IGNORECASE,
)

# Encerramento/negativa curta ("não precisa mais nada", "não, obrigado", "só
# isso"). Conversa real de 2026-10-20: a resposta anterior terminava com "Quer
# que eu detalhe mais alguma coisa?", o roteador tratou o "não" como resposta a
# uma clarificação do especialista e reabriu a rotina.
_ENCERRAMENTO_RE = re.compile(
    r"^\s*(n[aã]o|nada|s[oó] isso|t[aá] bom|tudo certo|entendi)"
    r"(\s*,?\s*(precisa|obrigad[oa]|valeu|mais nada|nada|n[aã]o|por enquanto|era s[oó] isso|s[oó] isso))*"
    r"\s*[!.?]*\s*$",
    re.IGNORECASE,
)
_RESPOSTA_ENCERRAMENTO = (
    "Tudo bem! Se precisar de algo sobre produto, ingrediente ou rotina, é só me chamar."
)

# O app manda o produto aberto na tela como prefixo da mensagem: "Sobre CeraVe
# Creme Hidratante: <pergunta>". Esse prefixo é CONTEXTO, não a pergunta — sem
# separá-lo, "creme"/"hidratante" faziam `rota_por_palavras` forçar a rota
# produto em qualquer mensagem enviada daquela tela, inclusive "o que eu acabei
# de falar?" (conversa real de 2026-10-20, que travou repetindo a mesma ficha
# do produto).
_PREFIXO_PRODUTO_RE = re.compile(
    r"^\s*sobre\s+(?P<produto>[^:\n]{2,120}?)\s*:\s*(?P<pergunta>\S.*)$", re.IGNORECASE | re.DOTALL
)


# Rede de segurança: modelos pequenos às vezes RESPONDEM em vez de rotear um pedido claramente de
# domínio. Só entra quando o LLM não devolveu ROUTE=; é conservadora de propósito.
_RE_ROTINA = re.compile(r"\brotina\b", re.IGNORECASE)
_RE_INGREDIENTE = re.compile(
    r"\b(ingrediente|niacinamida|nicotinamida|retinol|ceramidas?|hialur[oô]nico|vitamina\s*c|salic[ií]lico|"
    r"glic[oó]lico|pantenol|parfum|fragr[aâ]ncia)\b", re.IGNORECASE)
_RE_PRODUTO = re.compile(
    r"\b(produtos?|shampoo|condicionador|hidratante|s[eé]rum|protetor solar|creme|m[aá]scara|"
    r"limpeza|sabonete|filtro solar)\b", re.IGNORECASE)
_RE_FAQ = re.compile(
    r"(venus.{0,40}\b(score|funciona|regras?|pol[ií]tica|privacidade|dados)\b|"
    r"\b(score|funciona|regras?|pol[ií]tica|privacidade|dados)\b.{0,40}venus|"
    r"\b(meus dados|privacidade|lgpd|seus termos|como funciona o score)\b)", re.IGNORECASE)

# Pedidos sobre o que está CADASTRADO na conta (perfil, alergias cadastradas,
# favoritos, listas): só o agente de rotina tem essas tools. Aplicado MESMO
# quando o LLM escolheu outra rota — no teste de 2026-09-26 "quais são meus
# favoritos?" ia para produto (que respondia "você não tem favoritos") e "qual
# meu tipo de pele segundo meu perfil?" era respondida pelo próprio roteador,
# que inventava o dado.
_RE_DADOS_DA_CONTA = re.compile(
    r"\b(favorit\w*|minhas? listas?|listas? (de produtos|salvas?)|(meu|no|do) perfil|"
    r"alergias? cadastrad\w*|minhas alergias)\b",
    re.IGNORECASE,
)
# "Qual é meu nome?" sem o nome ter aparecido na conversa: o nome do cadastro vem
# do perfil (agente de rotina). O roteador respondia "Seu nome é," em branco ou
# "não lembro seu nome" (chatbot real, 2026-10-28).
_RE_PERGUNTA_NOME = re.compile(r"\b(qual|como)\b[^?]{0,15}\bmeu nome\b|\bcomo (eu )?me chamo\b", re.IGNORECASE)
_RE_DISSE_O_NOME = re.compile(r"\b(meu nome [ée]|me chamo|pode me chamar de)\b", re.IGNORECASE)
# Linhas de contexto que o roteador recebe e às vezes ecoa na resposta direta
# ("MEMORIA_USUARIO={}" chegou ao usuário no chatbot real, 2026-10-28).
_LINHA_DE_CONTEXTO_RE = re.compile(
    r"^[ \t]*(MEMORIA_USUARIO|PRODUTO_EM_TELA|USER_ID_POSTGRES)[ \t]*=.*(?:\n|$)", re.MULTILINE
)
# "Ingredientes/composição DO [produto]": pergunta de produto, não de ingrediente
# isolado (só o agente de produto tem `get_product_ingredients`).
_RE_COMPOSICAO_DE = re.compile(r"\b(ingredientes|composi[cç][aã]o|f[oó]rmula)\s+d[oa]s?\b", re.IGNORECASE)


# Rede de segurança contra alucinação na resposta DIRETA do roteador (sem tools, sem juiz): se ela
# afirma fatos de produto/ingrediente (nota, %, "contém"), descarta e usa uma resposta segura.
_RE_FATO_DE_PRODUTO = re.compile(
    r"(\d+\s*/\s*100|\bscore\b[^.\n]{0,30}\d|\d+[.,]?\d*\s*%|\bn[ãa]o cont[eé]m\b|\bcont[eé]m\b|"
    r"confirma[cç][ãa]o no sistema)", re.IGNORECASE)
# "o que eu acabei de falar?", "do que a gente tava falando?", "prompt anterior".
_RE_PERGUNTA_SOBRE_CONVERSA = re.compile(
    r"\b(o que|oq|do que|sobre o que)\b.{0,30}\b(eu|a gente|n[oó]s|voc[eê]|vc)\b.{0,30}"
    r"\b(falei|falar|disse|dizer|falando|conversando|perguntei|respondeu|falou)\b"
    r"|\b(prompt|mensagem|pergunta|resposta) anterior\b",
    re.IGNORECASE,
)
_RESPOSTA_DIRETA_SEGURA = (
    "Anotei! Posso te ajudar com produtos, ingredientes ou a sua rotina de skincare e haircare "
    "— o que você quer saber?"
)


def e_small_talk(mensagem: str) -> bool:
    """True se a mensagem é só saudação/agradecimento/despedida/encerramento."""
    return bool(_SMALL_TALK_RE.match(mensagem or "")) or e_encerramento(mensagem)


def e_encerramento(mensagem: str) -> bool:
    """True se a mensagem é só uma negativa/encerramento ("não precisa mais nada")."""
    return bool(_ENCERRAMENTO_RE.match(mensagem or ""))


def separar_produto_em_contexto(mensagem: str) -> tuple[str | None, str]:
    """`(produto, pergunta)` a partir de "Sobre <produto>: <pergunta>"; sem o
    prefixo, `(None, mensagem)`."""
    match = _PREFIXO_PRODUTO_RE.match(mensagem or "")
    if not match:
        return None, mensagem or ""
    return match.group("produto").strip(), match.group("pergunta").strip()


def _recuperar_de_tool_call_alucinada(erro: Exception) -> str | None:
    """Recupera ROUTE=/PERGUNTA_ORIGINAL= de dentro do erro 400 da Groq
    quando o gpt-oss-20b alucina uma tool call nativa (ex.: um "tool"
    chamado "router" tentando emitir o protocolo como argumentos) mesmo sem
    nenhuma tool vinculada ao client — a Groq rejeita a chamada inteira com
    `400 "Tool choice is none, but model called a tool"` (`code ==
    "tool_use_failed"`) nesse caso.

    Em vez de só tentar de novo (a alucinação não é 100% determinística nem
    a `temperature=0.0` — retries às vezes se repetem, cada um consumindo
    mais uma chamada da cota), a Groq devolve o `failed_generation` no corpo
    do erro com a decisão que o modelo já tinha tomado; extrai ela dali e
    reconstrói o protocolo em texto puro, evitando desperdiçar a resposta.

    Devolve `None` (nunca levanta) se o erro não for esse caso específico ou
    se o corpo não tiver o formato esperado — quem chama cai no retry normal.
    """
    corpo = getattr(erro, "body", None)
    if not isinstance(corpo, dict):
        return None
    detalhe = corpo.get("error")
    if not isinstance(detalhe, dict) or detalhe.get("code") != "tool_use_failed":
        return None
    bruto = detalhe.get("failed_generation")
    if not bruto:
        return None
    try:
        argumentos = json.loads(bruto)["arguments"]
        rota = argumentos["ROUTE"]
        pergunta = argumentos["PERGUNTA_ORIGINAL"]
    except (TypeError, ValueError, KeyError):
        return None

    logger.warning(
        "LLM roteador alucinou uma tool call (ROUTE=%s); recuperando do corpo do "
        "erro em vez de tentar de novo",
        rota,
    )
    return f"ROUTE={rota}\nPERGUNTA_ORIGINAL={pergunta}"


def rota_por_palavras(mensagem: str) -> str | None:
    """Rota inferida por palavras-chave (ou None se não for clara)."""
    if _RE_DADOS_DA_CONTA.search(mensagem):
        return "rotina"
    if _RE_FAQ.search(mensagem):
        return "faq"
    if _RE_ROTINA.search(mensagem):
        return "rotina"
    if _RE_INGREDIENTE.search(mensagem):
        return "ingrediente"
    if _RE_PRODUTO.search(mensagem):
        return "produto"
    return None


def _invocar_roteador(mensagens: list) -> str:
    """Chama o LLM roteador, com uma nova tentativa se a primeira levantar.

    Bug de content vazio: ver `get_llm_rapido`. Em vez de deixar a exceção
    derrubar o grafo inteiro, tenta mais uma vez e, se falhar de novo,
    devolve `""`. Antes de cada nova tentativa, tenta recuperar a decisão do
    corpo do erro (ver `_recuperar_de_tool_call_alucinada`)."""
    for tentativa in range(1, _TENTATIVAS_LLM_ROTEADOR + 1):
        try:
            resposta = get_llm_rapido().invoke(mensagens)
        except Exception as erro:
            recuperado = _recuperar_de_tool_call_alucinada(erro)
            if recuperado is not None:
                return recuperado
            if tentativa < _TENTATIVAS_LLM_ROTEADOR:
                logger.warning("Falha ao chamar o LLM roteador; tentando novamente uma vez", exc_info=True)
                continue
            logger.exception("Segunda tentativa do LLM roteador também falhou")
            return ""
        return (resposta.content or "").strip()
    return ""


def _mensagens_para_o_roteador(estado: EstadoVenus) -> list:
    """Prompt do roteador + histórico da conversa + a mensagem deste turno."""
    mensagem = estado.get("mensagem_anonimizada") or estado.get("mensagem_usuario", "")
    produto, pergunta = separar_produto_em_contexto(mensagem)
    if produto:
        mensagem = f"PRODUTO_EM_TELA={produto}\n{pergunta}"
    memorias = estado.get("memorias_usuario")
    if memorias:
        mensagem = f"MEMORIA_USUARIO={json.dumps(memorias, ensure_ascii=False)}\n{mensagem}"

    # O guardrail de entrada já pôs a mensagem deste turno no fim do
    # histórico; ela sai daqui para não aparecer duas vezes no prompt.
    historico = list(estado.get("historico") or [])
    if historico:
        historico = historico[:-1]
    return [("system", com_data_atual(ROUTER_PROMPT_COMPLETO)), *historico, ("human", mensagem)]


def _extrair_reacao(texto: str) -> tuple[str | None, str]:
    """`(valor da marca REACAO= em minúsculas ou None, texto sem a linha)`."""
    match = _REACAO_RE.search(texto)
    if not match:
        return None, texto
    return match.group(1).lower(), _REACAO_RE.sub("", texto).strip()


def _rota_do_texto(texto: str) -> str | None:
    match_rota = _ROUTE_RE.search(texto)
    return match_rota.group(1).strip().lower() if match_rota else None


def _rota_obrigatoria(rota: str | None, mensagem_usuario: str) -> str | None:
    """Rota que a mensagem exige independentemente da escolha do LLM, ou `None`.
    Não mexe em `faq`: "o Venus guarda meus favoritos?" é dúvida de privacidade."""
    if rota != "faq" and _RE_DADOS_DA_CONTA.search(mensagem_usuario):
        return "rotina"
    if rota == "ingrediente" and _RE_COMPOSICAO_DE.search(mensagem_usuario) and _RE_PRODUTO.search(mensagem_usuario):
        return "produto"
    return None


def _encaminhar(rota: str, mensagem_usuario: str) -> str:
    return f"ROUTE={rota}\nPERGUNTA_ORIGINAL={mensagem_usuario}"


def _fatos_vem_do_historico(texto: str, mensagem_usuario: str, historico: str) -> bool:
    """Resposta direta a "o que a gente estava falando?" pode repetir fatos já
    ditos na conversa: aceita se TODOS os números que ela cita aparecem no
    histórico. Sem isso, a recapitulação virava "Anotei! Posso te ajudar..."
    (reprodução da conversa de 2026-10-20)."""
    if not historico or not _RE_PERGUNTA_SOBRE_CONVERSA.search(mensagem_usuario):
        return False
    return all(numero in historico for numero in re.findall(r"\d+", texto))


def _aplicar_redes_de_seguranca(
    rota: str | None, texto: str, mensagem_usuario: str, historico: str = ""
) -> tuple[str | None, str]:
    """Corrige, por regra, os erros conhecidos do LLM roteador. Devolve
    `(rota, texto)` já corrigidos. `historico` é o texto das mensagens
    anteriores (ver `_fatos_vem_do_historico`)."""
    eh_small_talk = e_small_talk(mensagem_usuario)

    obrigatoria = _rota_obrigatoria(rota, mensagem_usuario)
    if obrigatoria and obrigatoria != rota:
        logger.warning("Roteador escolheu %s; rota %s forçada por palavras-chave", rota, obrigatoria)
        return obrigatoria, _encaminhar(obrigatoria, mensagem_usuario)

    if rota not in _ROTAS_VALIDAS and not eh_small_talk:
        sugerida = rota_por_palavras(mensagem_usuario)
        if sugerida:
            logger.warning("Roteador não devolveu ROUTE=; rota %s inferida por palavras-chave", sugerida)
            rota = sugerida
            texto = _encaminhar(sugerida, mensagem_usuario)

    if rota in _ROTAS_VALIDAS and eh_small_talk:
        # LLM roteou uma saudação pura para um especialista — descarta a rota
        # e responde direto (ver `_SMALL_TALK_RE`).
        logger.warning("Roteador mandou small talk para %s; forçando resposta direta", rota)
        rota = None
        texto = _RESPOSTA_ENCERRAMENTO if e_encerramento(mensagem_usuario) else ""

    if (
        rota not in _ROTAS_VALIDAS
        and texto
        and _RE_FATO_DE_PRODUTO.search(texto)
        and not _fatos_vem_do_historico(texto, mensagem_usuario, historico)
    ):
        logger.warning("Resposta direta do roteador afirmava fatos de produto; substituída por resposta segura")
        texto = _RESPOSTA_DIRETA_SEGURA

    return rota, texto


_RE_FORA_DE_ESCOPO = re.compile(
    r"fora d[oe] (que eu|meu|escopo|assunto)|n[ãa]o consigo te ajudar com isso", re.IGNORECASE
)
_TAMANHO_MAXIMO_CITACAO = 220


_RE_PEDE_PRODUTOS_NOVOS = re.compile(
    r"\b(escolh|sugir|suger|recomend|indiq|indica)\w*\b[^.?!]{0,40}\bprodutos?\b", re.IGNORECASE
)
_RE_ACEITE_CURTO = re.compile(r"^\s*(sim|pode|quero|claro|bora|isso|ok|por favor)\b", re.IGNORECASE)
_TAMANHO_MAXIMO_ACEITE = 40
_RE_OFERTA_DE_SUGESTAO = re.compile(
    r"\b(sugir|suger|indic|recomend)\w*\b[^.?!]{0,80}\b(produtos?|op[çc][õo]es|alternativas?|limpeza|protetor|hidratante)",
    re.IGNORECASE,
)


def _aceita_oferta_de_sugestao(pergunta: str, estado: EstadoVenus) -> bool:
    """"sim" (ou parecido, curto) logo depois de a Venus oferecer sugerir produtos."""
    if len(pergunta) > _TAMANHO_MAXIMO_ACEITE or not _RE_ACEITE_CURTO.match(pergunta):
        return False
    anteriores = list(estado.get("historico") or [])[:-1]
    ultima_da_venus = next((m for m in reversed(anteriores) if getattr(m, "type", "") == "ai"), None)
    return ultima_da_venus is not None and bool(_RE_OFERTA_DE_SUGESTAO.search(str(ultima_da_venus.content)))


def _recapitulacao(estado: EstadoVenus) -> str | None:
    """A última mensagem do usuário antes desta (sem o prefixo do produto em
    tela) e o começo da resposta da Venus a ela, ou `None` sem histórico."""
    mensagens = list(estado.get("historico") or [])[:-1]
    for indice in range(len(mensagens) - 1, -1, -1):
        if getattr(mensagens[indice], "type", "") != "human":
            continue
        _, anterior = separar_produto_em_contexto(str(mensagens[indice].content))
        texto = f'Na mensagem anterior, você disse: "{anterior.strip()}".'
        resposta = next((m for m in mensagens[indice + 1:] if getattr(m, "type", "") == "ai"), None)
        if resposta is not None:
            trecho = " ".join(str(resposta.content).split())
            if len(trecho) > _TAMANHO_MAXIMO_CITACAO:
                trecho = trecho[:_TAMANHO_MAXIMO_CITACAO].rsplit(" ", 1)[0] + "…"
            texto += f' E eu respondi: "{trecho}"'
        return texto
    return None


def _pergunta_o_nome_do_cadastro(pergunta: str, estado: EstadoVenus) -> bool:
    """"Qual é meu nome?" quando o nome não foi dito nesta conversa nem está na
    memória: quem responde é o perfil (`get_user_profile`)."""
    if not _RE_PERGUNTA_NOME.search(pergunta) or not estado.get("usuario_id_postgres"):
        return False
    if (estado.get("memorias_usuario") or {}).get("nome"):
        return False
    anteriores = list(estado.get("historico") or [])[:-1]
    return not any(
        getattr(m, "type", "") == "human" and _RE_DISSE_O_NOME.search(str(m.content)) for m in anteriores
    )


def no_roteador(estado: EstadoVenus) -> EstadoVenus:
    """Chama o LLM roteador com `ROUTER_PROMPT_COMPLETO` e extrai o
    protocolo `ROUTE=.../PERGUNTA_ORIGINAL=...` (ou responde diretamente em
    caso de small talk/fora de escopo).
    """
    # A mensagem anonimizada é a que segue para os especialistas: CPF, e-mail
    # etc. não podem voltar pelas redes de segurança nem pelo fallback.
    mensagem_usuario = estado.get("mensagem_anonimizada") or estado.get("mensagem_usuario", "")
    # As redes de segurança por palavra-chave olham só a pergunta, nunca o nome
    # do produto aberto na tela (ver `_PREFIXO_PRODUTO_RE`).
    produto_em_contexto, pergunta_do_usuario = separar_produto_em_contexto(mensagem_usuario)
    pendente = estado.get("agendamento_pendente")
    if pendente and (eh_confirmacao(pergunta_do_usuario) or eh_negacao(pergunta_do_usuario)):
        # Resposta a uma proposta de agendamento: quem decide é o código.
        return {
            "rota": "rotina", "pergunta_original": pergunta_do_usuario,
            "produto_em_contexto": produto_em_contexto, "expressao": "neutra",
        }
    mensagens = _mensagens_para_o_roteador(estado)

    texto = _invocar_roteador(mensagens)
    if not texto:
        # Falha pontual do LLM (conteúdo vazio); tenta mais uma vez antes de
        # decidir — o retry passa pelo mesmo parsing de ROUTE= abaixo, então
        # se ele vier com uma rota válida isso não vira texto cru pro usuário.
        texto = _invocar_roteador(mensagens)

    reacao, texto = _extrair_reacao(texto)
    texto_do_llm = texto
    historico = " ".join(str(getattr(m, "content", m)) for m in list(estado.get("historico") or [])[:-1])
    rota, texto = _aplicar_redes_de_seguranca(_rota_do_texto(texto), texto, pergunta_do_usuario, historico)
    if (
        rota not in _ROTAS_VALIDAS
        and _RE_PERGUNTA_SOBRE_CONVERSA.search(pergunta_do_usuario)
        and (not texto or _RE_FORA_DE_ESCOPO.search(texto))
    ):
        # "oq eu acabei de falar no prompt anterior?" virava "essa pergunta está
        # fora do que eu consigo te ajudar" em várias rodadas do chatbot
        # (2026-10-21 a 26): a resposta sai do próprio histórico.
        texto = _recapitulacao(estado) or texto
    if (
        rota != "produto"
        and _RE_PEDE_PRODUTOS_NOVOS.search(pergunta_do_usuario)
        and not _RE_DADOS_DA_CONTA.search(pergunta_do_usuario)
    ):
        # "escolha dois produtos e adicione pra mim" ia para a rotina, que não
        # busca no catálogo e inventava produtos (chatbot real, 2026-10-26).
        logger.warning("Roteador escolheu %s; pedido de produtos novos vai para produto", rota)
        rota = "produto"
    if rota != "produto" and _aceita_oferta_de_sugestao(pergunta_do_usuario, estado):
        # "Quer que eu sugira produtos para limpeza e protetor solar?" -> "sim" ia
        # de novo para a rotina, que repetia a mesma pergunta (chatbot real,
        # 2026-10-26): quem sugere produto do catálogo é o especialista de produto.
        logger.warning("Roteador escolheu %s; 'sim' a uma oferta de sugestão vai para produto", rota)
        rota = "produto"
    if rota not in _ROTAS_VALIDAS and _pergunta_o_nome_do_cadastro(pergunta_do_usuario, estado):
        logger.warning("Roteador respondeu direto; pergunta pelo nome do cadastro vai para rotina")
        rota = "rotina"

    if rota not in _ROTAS_VALIDAS:
        # Small talk ou fora de escopo: o próprio roteador já formulou a
        # resposta final ao usuário — segue direto para o guardrail de saída.
        # Uma proposta de agendamento sem resposta expira aqui. A cara só é
        # `magoada` se o usuário vai ver a reação que o roteador escreveu.
        magoada = reacao == "magoada" and bool(texto) and texto == texto_do_llm
        texto = _MARCADOR_RE.sub("", _LINHA_DE_CONTEXTO_RE.sub("", texto)).strip()
        return {
            "rota": None,
            "produto_em_contexto": produto_em_contexto,
            "resposta_final": texto or _RESPOSTA_DIRETA_FALLBACK,
            "agendamento_pendente": None,
            "expressao": "magoada" if magoada else "neutra",
        }

    # PERGUNTA_ORIGINAL é sempre a mensagem REAL do usuário, nunca o que o LLM
    # ecoou no protocolo: ele colava colchetes ("[Monta minha rotina...]") e,
    # pior, trocava a mensagem por uma de turnos anteriores ("cadastrei os
    # produtos..." virou "qual vc recomendaria?" — reprodução de 2026-10-20). O
    # contexto que ele tentava embutir chega ao especialista por
    # HISTORICO_RECENTE/PRODUTO_EM_CONTEXTO.
    return {  # type: ignore[typeddict-item]
        "rota": rota,
        "pergunta_original": pergunta_do_usuario,
        "produto_em_contexto": produto_em_contexto,
        "agendamento_pendente": None,
        "expressao": "neutra",
    }


def decidir_especialista(estado: EstadoVenus) -> DecisaoRoteador:
    """Aresta condicional: lê `estado['rota']` e decide o próximo nó.

    Casa com as chaves usadas em `flows/venus_flow.py` — "direto" cobre o
    caso em que o roteador já respondeu (small talk/fora de escopo).
    """
    rota = estado.get("rota")
    if rota in _ROTAS_VALIDAS:
        return rota  # type: ignore[return-value]
    return "direto"
