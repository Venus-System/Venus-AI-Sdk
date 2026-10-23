"""Nó Orquestrador: transforma o JSON do especialista na resposta final."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from venus_sdk.alergias import termo_bate, termos_da_alergia
from venus_sdk.llm.models import extrair_texto_resposta, get_llm_orquestrador
from venus_sdk.nodes._amarracao_produtos import mesmo_nome, nomes_por_id
from venus_sdk.nodes._evidencias import argumentos_da_evidencia, dados_da_evidencia
from venus_sdk.prompts.comum import com_data_atual
from venus_sdk.prompts.orquestrador import ORQUESTRADOR_PROMPT_COMPLETO
from venus_sdk.nodes.state import EstadoVenus
from venus_sdk.texto import remover_acentos

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
    "é o melhor sem chutar. Se você me contar o que procura (seu tipo de pele ou cabelo e o "
    "objetivo), eu te ajudo a escolher entre eles — ou posso ver algum em particular."
)
_CONVITE_PARA_DETALHAR = "Quer que eu detalhe mais alguma coisa?"

# Quantos itens de cada busca entram na resposta montada sem o LLM.
_MAX_PRODUTOS_LISTADOS = 6
_MAX_INGREDIENTES_LISTADOS = 5

_TIPOS_DE_RESTRICAO = {"prohibited": "proibido", "restricted": "uso restrito"}
_NIVEIS_DE_RECOMENDACAO = {
    "contraindicated": "contraindicado",
    "not_recommended": "não recomendado",
    "acceptable": "aceitável",
    "recommended": "recomendado",
    "highly_recommended": "muito recomendado",
}

# Texto mais curto que isso não pode ser a tradução de uma resposta de verdade.
_TAMANHO_MINIMO_TEXTO_FINAL = 12

# Numa resposta sobre um produto que listou ingredientes, o texto final
# precisa citar pelo menos esta fração deles (tolera nome traduzido/grafado
# diferente pelo especialista, mas não uma lista trocada por 2 ou 3 exemplos).
_FRACAO_MINIMA_DE_INGREDIENTES_CITADOS = 0.8

# Listas só aparecem na resposta quando a pergunta pede por elas; senão, no
# máximo um comentário curto (ex.: "ele tem 19 ingredientes cadastrados").
_PEDE_INGREDIENTES_RE = re.compile(
    r"\b(ingredientes?|composi[cç][aã]o|f[oó]rmula|inci|o que (ele |ela )?(tem|leva|cont[eé]m))\b", re.IGNORECASE
)
_PEDE_FAVORITOS_RE = re.compile(r"favorit", re.IGNORECASE)
_PEDE_SUGESTAO_RE = re.compile(r"\b(recomend|sugir|sugest|indica)\w*", re.IGNORECASE)
_PERGUNTA_TARDE_RE = re.compile(r"\b(de|à|a|pela|na)\s+tarde\b", re.IGNORECASE)
_PERGUNTA_AGENDA_RE = re.compile(r"\b(ocupad[oa]|livre|agenda|compromisso)s?\b", re.IGNORECASE)
_PEDE_LISTAS_RE = re.compile(r"\blistas?\b", re.IGNORECASE)

# Antes do "sim", nada foi gravado: o texto não pode afirmar o contrário.
_AFIRMA_AGENDAMENTO_RE = re.compile(
    r"\b(agendei|agendad[oa]s?|marquei|coloquei na (sua )?agenda|adicionei (à|a|na) (sua )?agenda|"
    r"removi|tirei)\b", re.IGNORECASE,
)
_RESPOSTA_ANTES_DA_CONFIRMACAO = "Preparei o agendamento da sua rotina — só falta você confirmar."
# `[nome do produto]` é placeholder; `[FAQ](https://...)` é link markdown.
_PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{2,40}\](?!\()")
_ID_INTERNO_RE = re.compile(
    r"\s*\((?:product|ingredient|user|produto)_id\s*[:=]?\s*\d+\)|\s*\b(?:product|ingredient|user)_id\s*[:=]?\s*\d+",
    re.IGNORECASE,
)
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
    """Rotina montada por `suggest_routine` — inclusive quando ela NÃO saiu: sem
    isso, uma rotina vazia (todos os favoritos excluídos por alergia) virava
    "Seus produtos favoritos: X", como se X pudesse ser usado (conversa real de
    2026-10-20)."""
    dados = dados_da_evidencia(evidencias, "suggest_routine")
    if not isinstance(dados, dict):
        return None
    passos = dados.get("passos") or []
    excluidos = dados.get("excluidos_por_alergia") or []
    faltando = dados.get("sem_produto_para") or []
    if not passos and not excluidos and not faltando:
        return None
    if passos:
        lista = "; ".join(f"{passo['ordem']}) {passo['nome']}" for passo in passos)
        frases = [f"Montei sua rotina com o que você já tem salvo: {lista}."]
    else:
        frases = ["Ainda não consegui montar essa rotina só com os seus favoritos."]
    if excluidos:
        nomes = ", ".join(str(item.get("nome")) for item in excluidos if isinstance(item, dict))
        frases.append(f"Deixei de fora {nomes} por ter ingrediente que bate com as alergias que você declarou.")
    if faltando:
        frases.append(f"Falta um produto salvo para: {', '.join(map(str, faltando))}.")
    return " ".join(frases)


_TOOLS_DE_DETALHE_DO_PRODUTO = ("get_product", "get_product_score", "get_personalized_score", "get_product_ingredients")


def _produto_consultado(evidencias: list[dict] | None) -> Any:
    """`product_id` do produto que o agente consultou em detalhe, ou `None`."""
    for tool in _TOOLS_DE_DETALHE_DO_PRODUTO:
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


def _consultado_com_nome(evidencias: list[dict] | None, nome: str | None) -> Any:
    """`product_id` consultado em detalhe cujo nome é `nome` (o produto em tela), ou `None`."""
    if not nome:
        return None
    nomes = nomes_por_id(evidencias)
    for evidencia in evidencias or []:
        if evidencia.get("tool") not in _TOOLS_DE_DETALHE_DO_PRODUTO:
            continue
        product_id = (evidencia.get("argumentos") or {}).get("product_id")
        if product_id is not None and mesmo_nome(nomes.get(str(product_id), ""), nome):
            return product_id
    return None


def _detalhe_do_produto(
    evidencias: list[dict] | None, pergunta: str = "", preferido: str | None = None
) -> str | None:
    """Dados de UM produto consultado em detalhe (nome, nota, ingredientes) —
    só retornos de chamadas feitas para ESSE `product_id`. A lista de
    ingredientes só entra (completa) se a pergunta pediu; senão, só a
    contagem. Com vários consultados, prefere o `preferido` (produto em tela)."""
    product_id = _consultado_com_nome(evidencias, preferido) or _produto_consultado(evidencias)
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

    # A nota personalizada é o que diz se o produto é contraindicado para o
    # usuário — sem ela, "vc acabou de contraindicar esse produto" recebia só a
    # nota geral (chatbot real, 2026-10-21). O `summary` da tool já vem em português.
    pessoal = dados_da_evidencia(evidencias, "get_personalized_score", product_id=product_id)
    if isinstance(pessoal, dict) and pessoal.get("summary"):
        linhas.append(f"Pra você: {pessoal['summary']}")
    elif isinstance(pessoal, dict) and pessoal.get("final_score") is not None:
        nivel = _NIVEIS_DE_RECOMENDACAO.get(str(pessoal.get("recommendation_level")))
        linhas.append(f"Nota pra você: {pessoal['final_score']}/100" + (f" ({nivel})." if nivel else "."))

    ingredientes = dados_da_evidencia(evidencias, "get_product_ingredients", product_id=product_id)
    if isinstance(ingredientes, list) and ingredientes:
        if _PEDE_INGREDIENTES_RE.search(pergunta or ""):
            nomes = [str(i.get("common_name") or i.get("inci_name")) for i in ingredientes if isinstance(i, dict)]
            linhas.append(f"Ingredientes cadastrados, na ordem do rótulo: {', '.join(nomes)}.")
        else:
            linhas.append(f"Ele tem {len(ingredientes)} ingredientes cadastrados — quer que eu liste?")
    elif isinstance(ingredientes, dict) and ingredientes.get("encontrado") is False:
        linhas.append("Os ingredientes dele ainda não estão cadastrados aqui.")
    return "\n".join(linhas)


def _parte_produtos(
    evidencias: list[dict] | None, pergunta: str = "", *, sugestao: bool = False, excluir: str | None = None
) -> tuple[str | None, bool]:
    """`(texto, sem_dado)` — `sem_dado` indica que nenhum produto listado tem
    nota nem ingredientes cadastrados.

    Num pedido de sugestão (`sugestao=True`) lista os candidatos de TODAS as
    buscas, com a nota quando consultada, e deixa de fora `excluir` (o produto
    aberto na tela): antes, "sim, sugira" depois de um alerta sobre o CeraVe
    Creme Hidratante devolvia a ficha do próprio CeraVe Creme Hidratante."""
    if not sugestao:
        detalhe = _detalhe_do_produto(evidencias, pergunta, preferido=excluir)
        if detalhe:
            return detalhe, False
    produtos = _resultados_de_todas_as_buscas(evidencias) if sugestao else dados_da_evidencia(evidencias, "search_product")
    if not isinstance(produtos, list) or not produtos:
        return None, False
    excluido = (excluir or "").strip().lower()
    itens = [
        p for p in produtos
        if isinstance(p, dict) and "name" in p and str(p["name"]).strip().lower() != excluido
    ][:_MAX_PRODUTOS_LISTADOS]
    if not itens:
        return None, False
    linhas = "\n".join(f"- {p['name']} ({p['brand_name']}){_nota_listada(evidencias, p)}" for p in itens)
    sem_dado = not any(p.get("tem_score") or p.get("tem_ingredientes") for p in itens)
    return f"Olha o que encontrei no nosso catálogo:\n\n{linhas}", sem_dado


def _resultados_de_todas_as_buscas(evidencias: list[dict] | None) -> list[dict]:
    """Produtos de todas as chamadas de `search_product`, sem repetir `product_id`."""
    vistos: set[str] = set()
    produtos = []
    for evidencia in evidencias or []:
        if evidencia.get("tool") != "search_product":
            continue
        resultado = dados_da_evidencia([evidencia], "search_product")
        for produto in resultado if isinstance(resultado, list) else []:
            chave = str(produto.get("product_id")) if isinstance(produto, dict) else ""
            if chave and chave not in vistos:
                vistos.add(chave)
                produtos.append(produto)
    return produtos


def _nota_listada(evidencias: list[dict] | None, produto: dict) -> str:
    score = dados_da_evidencia(evidencias, "get_product_score", product_id=produto.get("product_id"))
    if isinstance(score, dict) and score.get("overall_score") is not None:
        return f" — nota geral {score['overall_score']}/100"
    return ""


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


def _parte_alergias(evidencias: list[dict] | None, *, cruzar_com_produto: bool = True) -> str | None:
    """Alergias declaradas — cruzadas com os ingredientes do produto consultado,
    quando houver (mesma regra de `suggest_routine`, ver `venus_sdk/alergias.py`).
    Só listar as alergias, sem dizer se o produto bate com elas, não respondia
    nada (conversa real de 2026-10-20)."""
    alergias = dados_da_evidencia(evidencias, "get_user_allergies")
    if isinstance(alergias, dict) and alergias.get("encontrado") is False and "alergias" in alergias:
        return "Você não declarou nenhuma alergia no seu cadastro."
    if not isinstance(alergias, list) or not alergias:
        return None
    nomes = [str(a.get("allergy_name")) for a in alergias if isinstance(a, dict) and a.get("allergy_name")]
    if not nomes:
        return None
    ingredientes = _ingredientes_com_nomes(evidencias) if cruzar_com_produto else []
    if not ingredientes:
        return f"Você declarou alergia a: {', '.join(nomes)}."
    conflitos = []
    for alergia in nomes:
        termos = termos_da_alergia(alergia)
        achado = next(
            (exibicao for exibicao, variantes in ingredientes
             if any(termo_bate(termo, variante) for termo in termos for variante in variantes)),
            None,
        )
        if achado:
            conflitos.append(f"{achado} (alergia a {alergia})")
    if conflitos:
        return f"Atenção: ele tem ingrediente que bate com o que você declarou: {'; '.join(conflitos)}."
    return f"Nenhum ingrediente cadastrado dele bate com as alergias que você declarou ({', '.join(nomes)})."


def _ingredientes_com_nomes(evidencias: list[dict] | None) -> list[tuple[str, list[str]]]:
    """`(nome para exibir, [INCI, nome comum])` de cada ingrediente do produto consultado."""
    product_id = _produto_consultado(evidencias)
    if product_id is None:
        return []
    ingredientes = dados_da_evidencia(evidencias, "get_product_ingredients", product_id=product_id)
    if not isinstance(ingredientes, list):
        return []
    saida = []
    for ingrediente in ingredientes:
        if not isinstance(ingrediente, dict):
            continue
        variantes = [str(nome) for nome in (ingrediente.get("inci_name"), ingrediente.get("common_name")) if nome]
        if variantes:
            saida.append((str(ingrediente.get("common_name") or ingrediente.get("inci_name")), variantes))
    return saida


def _avisos_de_horario(pergunta: str, evidencias: list[dict] | None = None) -> list[str]:
    """Respostas para as partes da pergunta que a rotina não cobre. Sem isso,
    "posso fazer algo às 22h ou estou ocupado? e de tarde?" recebia só a lista
    da rotina, sem resposta para a tarde nem para a agenda (chatbot real,
    2026-10-21)."""
    avisos = []
    if _PERGUNTA_AGENDA_RE.search(pergunta or ""):
        avisos.append(_aviso_da_agenda(evidencias))
    if _PERGUNTA_TARDE_RE.search(pergunta or ""):
        avisos.append("De tarde não tem rotina: eu monto rotinas só de manhã e de noite.")
    return avisos


def _aviso_da_agenda(evidencias: list[dict] | None) -> str:
    """O que `check_availability` devolveu nesta conversa — ou, sem a tool (agenda
    Google não configurada no grafo), que não há acesso à agenda."""
    consultas = [
        dados for dados in (
            dados_da_evidencia([evidencia], "check_availability")
            for evidencia in evidencias or [] if evidencia.get("tool") == "check_availability"
        ) if isinstance(dados, dict)
    ]
    if not consultas:
        return "Não tenho acesso à sua agenda, então não consigo dizer se você está livre nesse horário."
    if any(dados.get("ocupado") for dados in consultas):
        return "Pela sua agenda Google, você tem compromisso nesse horário."
    if all(dados.get("conectado") and dados.get("ocupado") is False for dados in consultas):
        return "Pela sua agenda Google, você está livre nesse horário."
    if any(dados.get("conectado") is False for dados in consultas):
        return "Sua agenda Google não está conectada aqui; dá para conectar pelo app para eu checar seus horários."
    return "Não consegui ver sua agenda agora, então não sei se você está livre nesse horário."


def _resposta_segura_sem_aprovacao(estado: EstadoVenus) -> str:
    """Quando o Juiz esgota as tentativas em produto/ingrediente/rotina, o texto do especialista
    (reprovado por inventar dado) NÃO chega ao usuário. Monta uma resposta só com o que as
    tools realmente devolveram."""
    evidencias = estado.get("evidencias_tools")
    pergunta = estado.get("pergunta_original") or estado.get("mensagem_usuario") or ""
    rotina_montada = _parte_rotina(evidencias)
    # Favoritos e listas só entram se a pergunta citou um deles (ou nenhum):
    # "quais são minhas listas?" não deve despejar os favoritos junto.
    pede_favoritos = bool(_PEDE_FAVORITOS_RE.search(pergunta))
    pede_listas = bool(_PEDE_LISTAS_RE.search(pergunta))
    sem_preferencia = not (pede_favoritos or pede_listas)
    dados_da_conta = [
        parte for parte in (
            rotina_montada,
            None if rotina_montada or not (pede_favoritos or sem_preferencia) else _parte_favoritos(evidencias),
            _parte_listas(evidencias) if pede_listas or sem_preferencia else None,
            _parte_perfil(evidencias),
        ) if parte
    ]
    # Numa pergunta sobre a conta, a busca de produto foi só um meio (achar o
    # id): listar o catálogo ali confunde mais do que ajuda.
    especialista = estado.get("resposta_especialista")
    # Pela pergunta também: "qual vc recomendaria?" com o especialista declarando
    # outra intenção recebia a ficha do 1º produto consultado — que, no chatbot
    # real (2026-10-21), era um produto contraindicado para o usuário.
    sugestao = (
        isinstance(especialista, dict) and especialista.get("intencao") == "sugerir"
    ) or bool(_PEDE_SUGESTAO_RE.search(pergunta))
    texto_produtos, sem_dado = (None, False) if dados_da_conta else _parte_produtos(
        evidencias, pergunta, sugestao=sugestao, excluir=estado.get("produto_em_contexto")
    )
    candidatas = [
        *dados_da_conta,
        texto_produtos,
        _parte_ingredientes(evidencias),
        _parte_alergias(evidencias, cruzar_com_produto=not sugestao),
    ]
    partes = [parte for parte in candidatas if parte]
    if estado.get("rota") == "rotina" and partes:
        partes += _avisos_de_horario(pergunta, evidencias)

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


def _montar_entrada_orquestrador(especialista_json: Any, aprovado: bool, pergunta: str = "") -> str:
    # A pergunta vai junto para o orquestrador saber o que resumir e se uma
    # lista (ingredientes, favoritos...) foi pedida ou só veio no JSON.
    entrada = (
        f"PERGUNTA_ORIGINAL={pergunta}\n"
        f"ESPECIALISTA_JSON={json.dumps(especialista_json, ensure_ascii=False)}"
    )
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
    # Recusa fixa escrita no código: vai como está, sem reescrita do LLM.
    if especialista_e_objeto and especialista_json.get("intencao") in ("nao_suportado", "agendamento"):
        return {"resposta_final": especialista_json.get("resposta") or _RESPOSTA_ORQUESTRADOR_FALLBACK}
    # Juiz reprovou (tentativas esgotadas) em domínio com dado no banco: nunca repassa o texto
    # reprovado (ele costuma conter invenção); usa só a evidência das tools.
    if not aprovado and _dominio_com_dado_no_banco(estado, especialista_json):
        return {"resposta_final": _resposta_segura_sem_aprovacao(estado)}

    pergunta = estado.get("pergunta_original") or estado.get("mensagem_usuario") or ""
    entrada = _montar_entrada_orquestrador(especialista_json, aprovado, pergunta)
    mensagens = [("system", com_data_atual(ORQUESTRADOR_PROMPT_COMPLETO)), ("human", entrada)]
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

    if texto and _omitiu_dados_das_tools(texto, estado):
        texto = _texto_aprovado_completo(especialista_json, estado) if aprovado else None
        texto = texto or _resposta_segura_sem_aprovacao(estado)

    texto = _sem_ids_internos(texto) or _RESPOSTA_ORQUESTRADOR_FALLBACK
    pendentes = estado.get("agendamento_pendente")
    if pendentes and _AFIRMA_AGENDAMENTO_RE.search(texto):
        logger.warning("Orquestrador afirmou um agendamento ainda não confirmado; texto substituído")
        texto = _RESPOSTA_ANTES_DA_CONFIRMACAO
    if pendentes:
        # O que será gravado é mostrado pelo código, não pelo LLM: o "sim"
        # confirma exatamente este texto.
        texto = f"{texto}\n\n{_pedido_de_confirmacao(pendentes)}"
    return {"resposta_final": texto}


def _pedido_de_confirmacao(propostas: list[dict]) -> str:
    from venus_sdk.tools.calendario import resumo_da_proposta

    itens = "\n".join(f"- {resumo_da_proposta(proposta)}" for proposta in propostas)
    return f"Vou fazer na sua agenda Google:\n{itens}\n\nPosso? Responda **sim** para confirmar ou **não** para cancelar."


def _texto_aprovado_completo(especialista_json: Any, estado: EstadoVenus) -> str | None:
    """Texto do especialista (já aprovado pelo Juiz) quando ELE traz todos os
    itens que o texto do LLM omitiu, ou `None`. Trocar direto pela resposta
    montada só das tools jogava fora o resto do conteúdo aprovado: no chatbot
    real (2026-10-21), "posso fazer algo às 22h? e de tarde?" perdeu a parte da
    tarde porque o LLM esqueceu um dos 3 produtos da rotina."""
    if not isinstance(especialista_json, dict):
        return None
    acompanhamento = especialista_json.get("esclarecer") or especialista_json.get("acompanhamento")
    partes = [
        str(parte).strip()
        for parte in (especialista_json.get("resposta"), especialista_json.get("recomendacao"), acompanhamento)
        if parte and str(parte).strip()
    ]
    texto = "\n\n".join(partes)
    if not texto or _omitiu_dados_das_tools(texto, estado):
        return None
    return texto


def _sem_ids_internos(texto: str) -> str:
    """Tira "(product_id 3)" e afins: id interno não é para o usuário (chatbot
    real, 2026-10-21, a resposta saiu com "CeraVe ... (product_id 3)")."""
    return _ID_INTERNO_RE.sub("", texto or "").strip()


def _omitiu_dados_das_tools(texto: str, estado: EstadoVenus) -> bool:
    """True se o texto do LLM deixou de fora dados que a resposta precisa
    trazer: itens da conta (rota rotina) ou a lista de ingredientes de um
    produto (rota produto). No teste de 2026-09-26 o orquestrador trocava a
    lista de 19 ingredientes aprovada pelo Juiz por 3 ingredientes comentados
    e uma afirmação de segurança que nenhuma tool fez."""
    evidencias = estado.get("evidencias_tools")
    rota = estado.get("rota")
    pergunta = estado.get("pergunta_original") or estado.get("mensagem_usuario") or ""
    if rota == "rotina":
        faltando = _itens_omitidos(texto, evidencias, pergunta)
        if faltando:
            logger.warning("Orquestrador omitiu itens da conta do usuário (%s); resposta montada das tools", faltando)
        return bool(faltando)
    if rota == "produto" and _PEDE_INGREDIENTES_RE.search(pergunta):
        ingredientes = _ingredientes_do_produto(evidencias)
        if not ingredientes:
            return False
        citados = [nome for nome in ingredientes if _compactar(nome) in _compactar(texto)]
        if len(citados) < _FRACAO_MINIMA_DE_INGREDIENTES_CITADOS * len(ingredientes):
            logger.warning(
                "Orquestrador citou %d de %d ingredientes; resposta montada das tools", len(citados), len(ingredientes)
            )
            return True
    return False


def _ingredientes_do_produto(evidencias: list[dict] | None) -> list[str]:
    product_id = _produto_consultado(evidencias)
    if product_id is None:
        return []
    ingredientes = dados_da_evidencia(evidencias, "get_product_ingredients", product_id=product_id)
    if not isinstance(ingredientes, list):
        return []
    return [str(i.get("common_name")) for i in ingredientes if isinstance(i, dict) and i.get("common_name")]


def _itens_obrigatorios(evidencias: list[dict] | None, pergunta: str = "") -> list[str]:
    """Nomes que uma resposta sobre a conta PRECISA citar: os passos da rotina
    montada ou, sem rotina, os favoritos e os itens das listas — só quando a
    pergunta pediu por eles."""
    rotina = dados_da_evidencia(evidencias, "suggest_routine")
    if isinstance(rotina, dict) and rotina.get("passos"):
        return [str(passo.get("nome")) for passo in rotina["passos"] if isinstance(passo, dict)]
    nomes: list[str] = []
    favoritos = dados_da_evidencia(evidencias, "get_user_favorites") if _PEDE_FAVORITOS_RE.search(pergunta) else None
    if isinstance(favoritos, list):
        nomes += [str(f.get("name")) for f in favoritos if isinstance(f, dict) and f.get("name")]
    listas = dados_da_evidencia(evidencias, "get_user_lists") if _PEDE_LISTAS_RE.search(pergunta) else None
    if isinstance(listas, list):
        nomes += [str(i.get("product_name")) for i in listas if isinstance(i, dict) and i.get("product_name")]
    return nomes


def _itens_omitidos(texto: str, evidencias: list[dict] | None, pergunta: str = "") -> list[str]:
    """Itens obrigatórios que não aparecem no texto (comparação sem espaços
    nem maiúsculas: "FPS 50" e "FPS50" contam como o mesmo)."""
    texto_compacto = _compactar(texto)
    return [nome for nome in _itens_obrigatorios(evidencias, pergunta) if not _item_citado(nome, texto_compacto)]


def _item_citado(nome: str, texto_compacto: str) -> bool:
    """Nome completo, ou sem a marca na frente quando sobram 2+ palavras
    ("Espuma Cremosa de Limpeza Hidratante") — mesma regra do Juiz
    (`nodes/juiz.py::_passo_citado`)."""
    if _compactar(nome) in texto_compacto:
        return True
    sem_marca = nome.split(" ", 1)[1] if " " in nome.strip() else ""
    return len(sem_marca.split()) >= 2 and _compactar(sem_marca) in texto_compacto


def _compactar(texto: str) -> str:
    """Sem espaços, acentos nem maiúsculas: "FPS 50" = "FPS50", "Álcool" = "alcool"."""
    return remover_acentos(re.sub(r"\s+", "", texto)).lower()
