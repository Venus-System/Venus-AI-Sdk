"""Nó Orquestrador: transforma o JSON do especialista na resposta final."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from venus_sdk.llm.models import extrair_texto_resposta, get_llm_orquestrador
from venus_sdk.nodes._evidencias import argumentos_da_evidencia, dados_da_evidencia
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
_CONVITE_PARA_DETALHAR = "Quer que eu detalhe mais alguma coisa?"

# Quantos itens de cada busca entram na resposta montada sem o LLM.
_MAX_PRODUTOS_LISTADOS = 6
_MAX_INGREDIENTES_LISTADOS = 5
_MAX_INGREDIENTES_DO_PRODUTO = 15

_TIPOS_DE_RESTRICAO = {"prohibited": "proibido", "restricted": "uso restrito"}

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


def _produto_consultado(evidencias: list[dict] | None) -> Any:
    """`product_id` do produto que o agente consultou em detalhe, ou `None`."""
    for tool in ("get_product", "get_product_score", "get_product_ingredients"):
        product_id = argumentos_da_evidencia(evidencias, tool).get("product_id")
        if product_id is not None:
            return product_id
    return None


def _cabecalho_do_produto(evidencias: list[dict] | None, product_id: Any) -> str | None:
    produto = dados_da_evidencia(evidencias, "get_product", product_id=product_id)
    if isinstance(produto, dict) and "name" in produto:
        return f"{produto['name']} ({produto.get('brand_name')}), da categoria {produto.get('category_name')}."
    busca = dados_da_evidencia(evidencias, "search_product")
    if not isinstance(busca, list):
        return None
    achado = next(
        (p for p in busca if isinstance(p, dict) and str(p.get("product_id")) == str(product_id)), None
    )
    return f"{achado['name']} ({achado.get('brand_name')})." if achado else None


def _detalhe_do_produto(evidencias: list[dict] | None) -> str | None:
    """Dados de UM produto consultado em detalhe (nome, nota, ingredientes) —
    só retornos de chamadas feitas para ESSE `product_id`."""
    product_id = _produto_consultado(evidencias)
    if product_id is None:
        return None
    cabecalho = _cabecalho_do_produto(evidencias, product_id)
    if cabecalho is None:
        return None
    linhas = [cabecalho]

    score = dados_da_evidencia(evidencias, "get_product_score", product_id=product_id)
    if isinstance(score, dict) and score.get("overall_score") is not None:
        linhas.append(f"Nota geral: {score['overall_score']}/100.")
    elif isinstance(score, dict) and score.get("encontrado") is False:
        linhas.append("Ele ainda não tem nota calculada aqui.")

    ingredientes = dados_da_evidencia(evidencias, "get_product_ingredients", product_id=product_id)
    if isinstance(ingredientes, list) and ingredientes:
        nomes = [
            str(i.get("common_name") or i.get("inci_name"))
            for i in ingredientes[:_MAX_INGREDIENTES_DO_PRODUTO] if isinstance(i, dict)
        ]
        continua = "…" if len(ingredientes) > _MAX_INGREDIENTES_DO_PRODUTO else "."
        linhas.append(f"Ingredientes cadastrados, na ordem do rótulo: {', '.join(nomes)}{continua}")
    elif isinstance(ingredientes, dict) and ingredientes.get("encontrado") is False:
        linhas.append("Os ingredientes dele ainda não estão cadastrados aqui.")
    return "\n".join(linhas)


def _parte_produtos(evidencias: list[dict] | None) -> tuple[str | None, bool]:
    """`(texto, sem_dado)` — `sem_dado` indica que nenhum produto listado tem
    nota nem ingredientes cadastrados."""
    detalhe = _detalhe_do_produto(evidencias)
    if detalhe:
        return detalhe, False
    produtos = dados_da_evidencia(evidencias, "search_product")
    if not isinstance(produtos, list) or not produtos:
        return None, False
    itens = [p for p in produtos[:_MAX_PRODUTOS_LISTADOS] if isinstance(p, dict) and "name" in p]
    if not itens:
        return None, False
    linhas = "\n".join(f"- {p['name']} ({p['brand_name']})" for p in itens)
    sem_dado = not any(p.get("tem_score") or p.get("tem_ingredientes") for p in itens)
    return f"Olha o que encontrei no nosso catálogo:\n\n{linhas}", sem_dado


def _propriedades_legiveis(propriedades: list) -> list[str]:
    valores = {
        p.get("property_name"): p.get("property_value")
        for p in propriedades if isinstance(p, dict)
    }
    linhas = []
    if valores.get("anvisa_status"):
        desde = f" desde {valores['anvisa_inicio_vigencia']}" if valores.get("anvisa_inicio_vigencia") else ""
        linhas.append(f"Situação na base de nomes INCI da ANVISA: {valores['anvisa_status']}{desde}.")
    quimica = [
        f"{rotulo} {valores[chave]}{sufixo}"
        for chave, rotulo, sufixo in (
            ("molecular_formula", "fórmula", ""),
            ("molecular_weight", "massa molar", " g/mol"),
            ("cas_number", "CAS", ""),
        )
        if valores.get(chave)
    ]
    if quimica:
        linhas.append(f"Dados químicos: {', '.join(quimica)}.")
    return linhas


def _detalhe_do_ingrediente(evidencias: list[dict] | None, ingrediente: dict) -> str | None:
    """Tudo o que as tools de detalhe trouxeram sobre o ingrediente, ou `None`
    se nenhuma delas foi consultada PARA ESSE `ingredient_id`."""
    ingredient_id = ingrediente.get("ingredient_id")
    if ingredient_id is None:
        return None
    efeitos = dados_da_evidencia(evidencias, "get_ingredient_effects", ingredient_id=ingredient_id)
    propriedades = dados_da_evidencia(evidencias, "get_ingredient_properties", ingredient_id=ingredient_id)
    regulacoes = dados_da_evidencia(evidencias, "get_ingredient_regulations", ingredient_id=ingredient_id)
    if efeitos is None and propriedades is None and regulacoes is None:
        return None

    inci = f" (INCI: {ingrediente['inci_name']})" if ingrediente.get("inci_name") else ""
    linhas = [f"{ingrediente.get('common_name')}{inci}."]
    if isinstance(propriedades, list):
        linhas += _propriedades_legiveis(propriedades)
    if isinstance(regulacoes, list) and regulacoes:
        restricoes = "; ".join(
            f"{_TIPOS_DE_RESTRICAO.get(r.get('restriction_type'), r.get('restriction_type'))} "
            f"({r.get('title')}, {r.get('country')})"
            for r in regulacoes if isinstance(r, dict)
        )
        linhas.append(f"Restrições regulatórias cadastradas: {restricoes}.")
    elif isinstance(regulacoes, dict) and regulacoes.get("encontrado") is False:
        linhas.append("Não há restrição regulatória cadastrada para ele aqui.")
    if isinstance(efeitos, list) and efeitos:
        nomes = ", ".join(str(e.get("effect_name")) for e in efeitos if isinstance(e, dict))
        linhas.append(f"Efeitos cadastrados: {nomes}.")
    elif isinstance(efeitos, dict) and efeitos.get("encontrado") is False:
        linhas.append("Os efeitos e benefícios dele ainda não estão cadastrados nas fontes do Venus.")
    return "\n".join(linhas)


def _parte_ingredientes(evidencias: list[dict] | None) -> str | None:
    ingredientes = dados_da_evidencia(evidencias, "search_ingredient")
    if not isinstance(ingredientes, list) or not ingredientes:
        return None
    primeiro = ingredientes[0]
    if len(ingredientes) == 1 and isinstance(primeiro, dict):
        detalhe = _detalhe_do_ingrediente(evidencias, primeiro)
        if detalhe:
            return detalhe
    nomes = ", ".join(
        str(i.get("common_name")) for i in ingredientes[:_MAX_INGREDIENTES_LISTADOS] if isinstance(i, dict)
    )
    return f"Encontrei no catálogo: {nomes}." if nomes else None


def _parte_acao_favorito(evidencias: list[dict] | None) -> str | None:
    """Resultado REAL de adicionar/remover favorito — nunca "feito" sem `ok`."""
    for tool, feito, verbo in (
        ("add_favorite", "Pronto, adicionei o produto aos seus favoritos.", "adicionar"),
        ("remove_favorite", "Pronto, removi o produto dos seus favoritos.", "remover"),
    ):
        resultado = dados_da_evidencia(evidencias, tool)
        if not isinstance(resultado, dict):
            continue
        if resultado.get("ok"):
            return feito
        if resultado.get("encontrado") is False:
            return f"Não consegui {verbo}: {resultado.get('mensagem')}."
        return f"Não consegui {verbo} o produto nos seus favoritos agora — deu um erro ao gravar. Pode tentar de novo mais tarde?"
    return None


def _parte_favoritos(evidencias: list[dict] | None) -> str | None:
    favoritos = dados_da_evidencia(evidencias, "get_user_favorites")
    if isinstance(favoritos, list) and favoritos:
        nomes = ", ".join(str(f.get("name")) for f in favoritos if isinstance(f, dict))
        return f"Seus produtos favoritos: {nomes}."
    if isinstance(favoritos, dict) and favoritos.get("encontrado") is False:
        return "Você ainda não tem produtos favoritos salvos."
    return None


def _parte_listas(evidencias: list[dict] | None) -> str | None:
    linhas = dados_da_evidencia(evidencias, "get_user_lists")
    if isinstance(linhas, dict) and linhas.get("encontrado") is False:
        return "Você ainda não tem listas de produtos salvas."
    if not isinstance(linhas, list) or not linhas:
        return None
    produtos_por_lista: dict[str, list[str]] = {}
    for linha in linhas:
        if isinstance(linha, dict):
            produtos = produtos_por_lista.setdefault(str(linha.get("list_name")), [])
            if linha.get("product_name"):
                produtos.append(str(linha["product_name"]))
    descricoes = [
        f"\"{nome}\": {', '.join(produtos)}." if produtos else f"\"{nome}\" (vazia)."
        for nome, produtos in produtos_por_lista.items()
    ]
    return "Suas listas salvas:\n" + "\n".join(f"- {descricao}" for descricao in descricoes)


def _parte_perfil(evidencias: list[dict] | None) -> str | None:
    perfil = dados_da_evidencia(evidencias, "get_user_profile")
    if not isinstance(perfil, dict) or perfil.get("encontrado") is False:
        return None
    campos = [
        f"{rotulo}: {perfil[chave]}"
        for chave, rotulo in (
            ("skin_type", "tipo de pele"),
            ("scalp_type", "couro cabeludo"),
            ("hair_pattern", "padrão do cabelo"),
        )
        if perfil.get(chave)
    ]
    if perfil.get("tags"):
        campos.append(f"tags: {', '.join(map(str, perfil['tags']))}")
    return f"No seu perfil: {'; '.join(campos)}." if campos else None


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
    dados_da_conta = [
        parte for parte in (
            _parte_acao_favorito(evidencias),
            _parte_rotina(evidencias),
            None if _parte_rotina(evidencias) else _parte_favoritos(evidencias),
            _parte_listas(evidencias),
            _parte_perfil(evidencias),
        ) if parte
    ]
    # Numa pergunta sobre a conta, a busca de produto foi só um meio (achar o
    # id): listar o catálogo ali confunde mais do que ajuda.
    texto_produtos, sem_dado = (None, False) if dados_da_conta else _parte_produtos(evidencias)
    candidatas = [
        *dados_da_conta,
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
    """`get_llm_orquestrador().invoke()` protegido contra exceção — sem
    isto, uma falha total de provedor (Gemini E o fallback Groq
    indisponíveis) subia crua até o `.ainvoke()` do grafo principal em vez
    de cair no fallback fixo, mesmo já existindo tratamento pro caso mais
    ameno de conteúdo vazio logo abaixo."""
    try:
        return extrair_texto_resposta(get_llm_orquestrador().invoke(mensagens)).strip()
    except Exception:
        logger.exception("Falha ao chamar o LLM do Orquestrador")
        return ""


def _montar_entrada_orquestrador(especialista_json: Any, aprovado: bool) -> str:
    entrada = f"ESPECIALISTA_JSON={json.dumps(especialista_json, ensure_ascii=False)}"
    # Chegou aqui via "esgotado" (ver `nodes/juiz.py`) sem aprovação plena.
    if not aprovado:
        entrada += "\n\n" + _NOTA_JUIZ_ESGOTADO
    return entrada


def _dominio_com_dado_no_banco(estado: EstadoVenus, especialista_json: Any) -> bool:
    """Pela ROTA do roteador (confiável) ou pelo `dominio` que o especialista
    declarou — no teste de 2026-09-26 o especialista de rotina declarou outro
    domínio e o texto reprovado chegou ao usuário."""
    dominio = especialista_json.get("dominio") if isinstance(especialista_json, dict) else None
    return estado.get("rota") in _DOMINIOS_COM_DADO_NO_BANCO or dominio in _DOMINIOS_COM_DADO_NO_BANCO


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
    if not aprovado and _dominio_com_dado_no_banco(estado, especialista_json):
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

    if texto and estado.get("rota") == "rotina":
        faltando = _itens_omitidos(texto, estado.get("evidencias_tools"))
        if faltando:
            logger.warning("Orquestrador omitiu itens da conta do usuário (%s); resposta montada das tools", faltando)
            texto = _resposta_segura_sem_aprovacao(estado)

    return {"resposta_final": texto or _RESPOSTA_ORQUESTRADOR_FALLBACK}


def _itens_obrigatorios(evidencias: list[dict] | None) -> list[str]:
    """Nomes que uma resposta sobre a conta PRECISA citar: os passos da rotina
    montada ou, sem rotina, os favoritos e os itens das listas consultados."""
    rotina = dados_da_evidencia(evidencias, "suggest_routine")
    if isinstance(rotina, dict) and rotina.get("passos"):
        return [str(passo.get("nome")) for passo in rotina["passos"] if isinstance(passo, dict)]
    nomes: list[str] = []
    favoritos = dados_da_evidencia(evidencias, "get_user_favorites")
    if isinstance(favoritos, list):
        nomes += [str(f.get("name")) for f in favoritos if isinstance(f, dict) and f.get("name")]
    listas = dados_da_evidencia(evidencias, "get_user_lists")
    if isinstance(listas, list):
        nomes += [str(i.get("product_name")) for i in listas if isinstance(i, dict) and i.get("product_name")]
    return nomes


def _itens_omitidos(texto: str, evidencias: list[dict] | None) -> list[str]:
    """Itens obrigatórios que não aparecem no texto (comparação sem espaços
    nem maiúsculas: "FPS 50" e "FPS50" contam como o mesmo)."""
    texto_compacto = _compactar(texto)
    return [nome for nome in _itens_obrigatorios(evidencias) if _compactar(nome) not in texto_compacto]


def _compactar(texto: str) -> str:
    return re.sub(r"\s+", "", texto).lower()
