"""Nó Orquestrador: transforma o JSON do especialista na resposta final."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from venus_sdk.alergias import conflitos
from venus_sdk.llm.models import extrair_texto_resposta, get_llm_orquestrador
from venus_sdk.nodes._amarracao_produtos import mesmo_nome, nomes_por_id
from venus_sdk.nodes._evidencias import (
    alergias_declaradas,
    argumentos_da_evidencia,
    dados_da_evidencia,
    rota_so_com_tools,
)
from venus_sdk.prompts.comum import com_data_atual
from venus_sdk.nodes.juiz import conferir_reescrita
from venus_sdk.prompts.orquestrador import ORQUESTRADOR_PROMPT_COMPLETO, TOM_PROMPT
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
# Na resposta montada das tools, fórmula/CAS/massa molar e o "você não declarou
# alergia" só entram quando a pergunta é sobre isso: "o que é niacinamida?"
# virava um bloco com dado químico e alergia (chatbot real, 2026-10-29).
_PEDE_QUIMICA_RE = re.compile(r"\b(f[oó]rmula|cas|massa molar|peso molecular|qu[ií]mic\w*|mol[eé]cul\w*)\b", re.IGNORECASE)
_PERGUNTA_SOBRE_ALERGIA_RE = re.compile(
    r"\b(alerg\w*|sensibilidade|posso usar|serve (pra|para) mim|seguro (pra|para) mim)\b", re.IGNORECASE
)

# Antes do "sim", nada foi gravado: o texto não pode afirmar o contrário.
_AFIRMA_AGENDAMENTO_RE = re.compile(
    r"\b(agendei|agendad[oa]s?|marquei|coloquei na (sua )?agenda|adicionei (à|a|na) (sua )?agenda|"
    r"removi|tirei)\b", re.IGNORECASE,
)
_RESPOSTA_ANTES_DA_CONFIRMACAO = "Preparei o agendamento da sua rotina — só falta você confirmar."
# `[nome do produto]` é placeholder; `[FAQ](https://...)` é link markdown.
_PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{2,40}\](?!\()")
# A IA nunca adiciona aos favoritos/listas (não há tool para isso): a frase que
# oferece fazer isso ("Quer que eu inclua esses produtos na sua lista?",
# "Confirma a adição desses dois produtos aos seus favoritos?") vira a orientação
# de salvar pelo app — no chatbot real (2026-10-26), o "sim" do usuário a essa
# oferta não tinha o que executar.
_OFERTA_DE_SALVAR_RE = re.compile(
    # O verbo de salvar é da própria Venus ("que eu inclua", "posso adicionar",
    # "confirma a adição") — "quer que eu sugira produtos para você salvar nos
    # favoritos?" é uma oferta válida e fica.
    r"[^.!?\n]*(?:\b(?:que eu|posso|devo)\s+(?:\w+\s+){0,2}?(?:adicion|inclu|salv|coloc|guard)\w*"
    r"|\bconfirma\w*\s+(?:a\s+)?(?:adi[cç][ãa]o|inclus[ãa]o))"
    r"[^.!?\n]*?\b(?:favorit|lista|rotina)\w*[^.!?\n]*[.!?]?",
    re.IGNORECASE,
)
_ORIENTACAO_SALVAR_NO_APP = "Se quiser usar algum deles na rotina, é só salvar nos favoritos pelo app."
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


_TOOLS_DE_DETALHE_DO_PRODUTO = ("get_product", "get_product_score", "get_product_ingredients")


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
    return " — sem nota cadastrada" if isinstance(score, dict) else ""


def _propriedades_legiveis(propriedades: list, *, com_quimica: bool) -> list[str]:
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
    if quimica and com_quimica:
        linhas.append(f"Dados químicos: {', '.join(quimica)}.")
    return linhas


def _detalhe_do_ingrediente(evidencias: list[dict] | None, ingrediente: dict, pergunta: str) -> str | None:
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
        linhas += _propriedades_legiveis(propriedades, com_quimica=bool(_PEDE_QUIMICA_RE.search(pergunta)))
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
        # A tool devolve um efeito por fonte: o mesmo nome vinha repetido 3 vezes.
        nomes = ", ".join(dict.fromkeys(str(e.get("effect_name")) for e in efeitos if isinstance(e, dict)))
        linhas.append(f"Efeitos cadastrados: {nomes}.")
    elif isinstance(efeitos, dict) and efeitos.get("encontrado") is False:
        linhas.append("Os efeitos e benefícios dele ainda não estão cadastrados nas fontes do Venus.")
    return "\n".join(linhas)


def _parte_ingredientes(evidencias: list[dict] | None, pergunta: str) -> str | None:
    ingredientes = dados_da_evidencia(evidencias, "search_ingredient")
    if not isinstance(ingredientes, list) or not ingredientes:
        return None
    primeiro = ingredientes[0]
    if len(ingredientes) == 1 and isinstance(primeiro, dict):
        detalhe = _detalhe_do_ingrediente(evidencias, primeiro, pergunta)
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


def _parte_alergias(evidencias: list[dict] | None, *, cruzar_com_produto: bool, citar_ausencia: bool) -> str | None:
    """Alergias declaradas — cruzadas com os ingredientes do produto consultado,
    quando houver (mesma regra de `suggest_routine`, ver `venus_sdk/alergias.py`).
    Só listar as alergias, sem dizer se o produto bate com elas, não respondia
    nada (conversa real de 2026-10-20)."""
    alergias = dados_da_evidencia(evidencias, "get_user_allergies")
    if isinstance(alergias, dict) and alergias.get("encontrado") is False and "alergias" in alergias:
        return "Você não declarou nenhuma alergia no seu cadastro." if citar_ausencia else None
    if not isinstance(alergias, list) or not alergias:
        return None
    nomes = alergias_declaradas(evidencias)
    if not nomes:
        return None
    ingredientes = _ingredientes_com_nomes(evidencias) if cruzar_com_produto else []
    if not ingredientes and not cruzar_com_produto:
        # Lista de sugestões sem ingredientes conferidos: no chatbot real
        # (2026-10-26) a lista trazia um produto com o ácido salicílico da alergia.
        return (f"Você declarou alergia a: {', '.join(nomes)}. Ainda não conferi os ingredientes desses produtos "
                "— antes de usar algum, me peça para checar.")
    if not ingredientes:
        return f"Você declarou alergia a: {', '.join(nomes)}."
    pares = conflitos(ingredientes, nomes)
    if pares:
        lista = "; ".join(f"{ingrediente} (alergia a {alergia})" for ingrediente, alergia in pares)
        return f"Atenção: ele tem ingrediente que bate com o que você declarou: {lista}."
    return f"Nenhum ingrediente cadastrado dele bate com as alergias que você declarou ({', '.join(nomes)})."


def _ingredientes_com_nomes(evidencias: list[dict] | None) -> list[dict]:
    """Ingredientes com nome do produto consultado."""
    product_id = _produto_consultado(evidencias)
    if product_id is None:
        return []
    ingredientes = dados_da_evidencia(evidencias, "get_product_ingredients", product_id=product_id)
    if not isinstance(ingredientes, list):
        return []
    return [i for i in ingredientes if isinstance(i, dict) and (i.get("inci_name") or i.get("common_name"))]


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


def _alertas_de_alergia(texto: str, evidencias: list[dict] | None) -> list[str]:
    """Alertas que a resposta final PRECISA trazer, calculados pelo código a
    partir das tools (alergias do cadastro × ingredientes consultados), não pelo
    LLM. O Orquestrador reescreve o JSON aprovado pelo Juiz e podia suavizar ou
    perder um alerta; estes entram depois dele, sempre com o mesmo texto.

    Vale para o produto consultado em detalhe, para os demais produtos com
    ingredientes consultados que o texto cita, e para o ingrediente buscado
    (um só resultado, ou citado no texto)."""
    alergias = alergias_declaradas(evidencias)
    if not alergias:
        return []
    texto_compacto = _compactar(texto)
    principal = _produto_consultado(evidencias)
    nomes = nomes_por_id(evidencias)
    alertas = []
    for evidencia in evidencias or []:
        if evidencia.get("tool") != "get_product_ingredients":
            continue
        product_id = (evidencia.get("argumentos") or {}).get("product_id")
        nome = nomes.get(str(product_id)) or ""
        if str(product_id) != str(principal) and not (nome and _compactar(nome) in texto_compacto):
            continue
        ingredientes = dados_da_evidencia([evidencia], "get_product_ingredients")
        pares = conflitos(ingredientes, alergias) if isinstance(ingredientes, list) else []
        if pares:
            achado, alergia = pares[0]
            alertas.append(f"Atenção: {nome or 'Esse produto'} tem {achado}, que bate com a sua alergia a {alergia} "
                           "(conferido no seu cadastro).")
    buscados = dados_da_evidencia(evidencias, "search_ingredient")
    if isinstance(buscados, list):
        for ingrediente in buscados:
            if not isinstance(ingrediente, dict):
                continue
            nome = str(ingrediente.get("common_name") or ingrediente.get("inci_name") or "")
            if len(buscados) > 1 and not (nome and _compactar(nome) in texto_compacto):
                continue
            pares = conflitos([ingrediente], alergias)
            if pares:
                alertas.append(f"Atenção: {pares[0][0]} bate com a sua alergia a {pares[0][1]} (conferido no seu cadastro).")
    return [alerta for alerta in dict.fromkeys(alertas) if _compactar(alerta) not in texto_compacto]


def _parte_sugestao_com_alergia(evidencias: list[dict] | None, excluir: str | None = None) -> str | None:
    """Sugestões de reserva para quem declarou alergia: separa os produtos cujos
    ingredientes foram conferidos (sem conflito / com conflito) dos que não
    foram. Sem isso, a lista de reserva punha lado a lado um produto conferido e
    outro com o ácido salicílico da alergia (chatbot real, 2026-10-26)."""
    alergias = alergias_declaradas(evidencias)
    if not alergias:
        return None
    excluido = (excluir or "").strip().lower()
    produtos = [
        p for p in _resultados_de_todas_as_buscas(evidencias)
        if isinstance(p, dict) and "name" in p and str(p["name"]).strip().lower() != excluido
    ]
    if not produtos:
        return None
    seguros, com_conflito, nao_conferidos = [], [], []
    for produto in produtos:
        ingredientes = dados_da_evidencia(evidencias, "get_product_ingredients", product_id=produto.get("product_id"))
        rotulo = f"{produto['name']} ({produto.get('brand_name')})"
        if not isinstance(ingredientes, list) or not ingredientes:
            nao_conferidos.append(rotulo)
        elif pares := conflitos(ingredientes, alergias):
            com_conflito.append(f"{rotulo}: tem {pares[0][0]} (alergia a {pares[0][1]})")
        else:
            seguros.append(f"- {rotulo}{_nota_listada(evidencias, produto)}")
    linhas = []
    if seguros:
        linhas.append(
            f"Conferi os ingredientes e estes não têm nada das alergias que você declarou ({', '.join(alergias)}):\n"
            + "\n".join(seguros[:_MAX_PRODUTOS_LISTADOS])
        )
    if com_conflito:
        linhas.append("Deixei de fora " + "; ".join(com_conflito) + ".")
    if nao_conferidos:
        restantes = ", ".join(nao_conferidos[: max(2, _MAX_PRODUTOS_LISTADOS - len(seguros))])
        linhas.append(f"Também achei {restantes}, mas ainda não conferi os ingredientes — me peça para checar "
                      f"antes de usar (você declarou alergia a {', '.join(alergias)}).")
    return "\n\n".join(linhas)


def _parte_checkup(evidencias: list[dict] | None) -> str | None:
    """Avisos do check-up da rotina (`check_routine_health`), com o motivo cadastrado."""
    dados = dados_da_evidencia(evidencias, "check_routine_health")
    if not isinstance(dados, dict) or not dados.get("checado"):
        return None
    motivos = [str(a.get("motivo")) for a in dados.get("avisos") or [] if isinstance(a, dict) and a.get("motivo")]
    if not motivos:
        return "O check-up da rotina não encontrou nenhum aviso."
    return "No check-up da rotina:\n" + "\n".join(f"- {motivo}" for motivo in dict.fromkeys(motivos))


def _parte_calculos(evidencias: list[dict] | None) -> str | None:
    """Resultados das tools de cálculo, no texto exato que elas mandam citar."""
    valores = []
    for evidencia in evidencias or []:
        dados = dados_da_evidencia([evidencia], str(evidencia.get("tool")))
        if isinstance(dados, dict) and dados.get("valor_para_citar"):
            valores.append(str(dados["valor_para_citar"]))
    return f"Resultado do cálculo: {'; '.join(dict.fromkeys(valores))}." if valores else None


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
            # Em produto/ingrediente o perfil é só contexto para personalizar: virando
            # "dado da conta", escondia a ficha do produto (chatbot real, 2026-10-28).
            _parte_perfil(evidencias) if estado.get("rota") == "rotina" else None,
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
    sugestao_com_alergia = (
        None if dados_da_conta or not sugestao
        else _parte_sugestao_com_alergia(evidencias, excluir=estado.get("produto_em_contexto"))
    )
    if sugestao_com_alergia:
        texto_produtos, sem_dado = sugestao_com_alergia, False
    else:
        texto_produtos, sem_dado = (None, False) if dados_da_conta else _parte_produtos(
            evidencias, pergunta, sugestao=sugestao, excluir=estado.get("produto_em_contexto")
        )
    candidatas = [
        *dados_da_conta,
        _parte_checkup(evidencias),
        _parte_calculos(evidencias),
        texto_produtos,
        _parte_ingredientes(evidencias, pergunta),
        None if sugestao_com_alergia else _parte_alergias(
            evidencias, cruzar_com_produto=not sugestao,
            citar_ausencia=estado.get("rota") == "rotina" or bool(_PERGUNTA_SOBRE_ALERGIA_RE.search(pergunta)),
        ),
    ]
    partes = [parte for parte in candidatas if parte]
    if estado.get("rota") == "rotina" and partes:
        partes += _avisos_de_horario(pergunta, evidencias)

    if not partes:
        return _SEGURA_GENERICA
    partes.append(_AVISO_SEM_NOTA_NEM_INGREDIENTES if sem_dado else _CONVITE_PARA_DETALHAR)
    return "\n\n".join(partes)


_PERGUNTA_TEM_RE = re.compile(r"\b(?:tem|cont[eé]m|possui)\s+(?:algum[a]?\s+)?([^?.,!]{3,40})", re.IGNORECASE)
# O que a ficha montada das tools responde. O resto recebe o aviso abaixo.
_FICHA_RESPONDE_RE = re.compile(
    r"\b(o que [eé]|oq [eé]|pra que serve|para que serve|nota|score|ingredientes?|composi[cç][aã]o|fala sobre|"
    r"fale sobre|me fala|sobre o|sobre a|indica|sugir|sugest|recomend|bom pra|boa pra|bom para|boa para|alerg)",
    re.IGNORECASE,
)
# Orientação fixa, escrita aqui: não é fato sobre produto ou ingrediente.
_PERGUNTA_DE_SAUDE_RE = re.compile(
    r"\b(gravid\w*|gr[aá]vida|gestan\w*|amament\w*|lactan\w*|rea[cç][aã]o|rea[cç][oõ]es|irrit\w*|ard[eê]ncia|coceira|"
    r"vermelh\w*)\b",
    re.IGNORECASE,
)
_ORIENTACAO_DE_SAUDE = "Em gravidez, amamentação ou reação na pele, converse com um profissional de saúde."
# `search_product` procura palavras: não sabe se o produto é indicado para o caso.
_AVISO_DA_BUSCA = (
    "Busquei as palavras do seu pedido no nome, na marca, na categoria e na descrição dos produtos. "
    "Isso não confirma que cada um seja indicado para o seu caso."
)
# O FAQ não passa pela reescrita: nela, o LLM acrescentava fatos que o Juiz aprovava.
_ABERTURA_DO_FAQ = "Olha o que a documentação do Venus diz sobre isso:"
_NAO_ENCONTRADO_NO_FAQ = "Não encontrei isso na documentação do Venus."
_FONTE_EXTERNA = "Encontrei isto em uma fonte externa, que o Venus não conferiu:"
# Texto longo (lista de ingredientes, manual) perde mais do que ganha ao ser reescrito.
_TAMANHO_MAXIMO_PARA_TOM = 900
_SEM_DADO_PARA_A_PERGUNTA = "Não tenho um dado cadastrado que responda exatamente a isso. O que tenho é:"

_FRASES_FIXAS = (
    _ORIENTACAO_DE_SAUDE, _AVISO_DA_BUSCA, "Não tenho um dado cadastrado que responda exatamente a isso",
)



def _resposta_sobre_ingrediente(pergunta: str, evidencias: list[dict] | None) -> str | None:
    """"O X tem fragrância?": confere na lista de ingredientes consultada, com a
    mesma regra das alergias (`venus_sdk/alergias.py`)."""
    achado = _PERGUNTA_TEM_RE.search(pergunta)
    ingredientes = _ingredientes_com_nomes(evidencias)
    if not achado or not ingredientes:
        return None
    termo = achado.group(1).strip()
    pares = conflitos(ingredientes, [termo])
    if pares:
        return f"Sim: {pares[0][0]} está na lista de ingredientes cadastrados."
    return f"Não: {termo} não aparece nos {len(ingredientes)} ingredientes cadastrados."


def _resposta_das_tools(estado: EstadoVenus) -> str:
    """Texto final sem nenhum texto de LLM: a ficha das tools e, se a pergunta
    pede algo que a ficha não responde, o aviso de que esse dado não existe."""
    pergunta = estado.get("pergunta_original") or estado.get("mensagem_usuario") or ""
    if estado.get("rota") == "faq":
        return _parte_faq(estado.get("resposta_especialista"))
    texto = _ficha_para_a_pergunta(estado, pergunta)
    return f"{_ORIENTACAO_DE_SAUDE}\n\n{texto}" if _PERGUNTA_DE_SAUDE_RE.search(pergunta) else texto


def _ficha_para_a_pergunta(estado: EstadoVenus, pergunta: str) -> str:
    evidencias = estado.get("evidencias_tools")
    rota = estado.get("rota")
    ficha = _resposta_segura_sem_aprovacao(estado)
    if rota == "rotina":
        return ficha
    direta = _resposta_sobre_ingrediente(pergunta, evidencias)
    if direta:
        return f"{direta}\n\n{ficha}"
    especialista = estado.get("resposta_especialista")
    sugestao = isinstance(especialista, dict) and especialista.get("intencao") == "sugerir"
    if rota == "produto" and sugestao and isinstance(dados_da_evidencia(evidencias, "search_product"), list):
        return f"{_AVISO_DA_BUSCA}\n\n{ficha}"
    return ficha if _FICHA_RESPONDE_RE.search(pergunta) else f"{_SEM_DADO_PARA_A_PERGUNTA}\n\n{ficha}"


def _parte_faq(especialista: Any) -> str:
    """O documento como está, com a fonte. Nada escrito por LLM."""
    fontes = especialista.get("fontes_a_mostrar") if isinstance(especialista, dict) else None
    if not fontes:
        return _NAO_ENCONTRADO_NO_FAQ
    externa = _FONTE_EXTERNA if any(item.get("externa") for item in fontes) else ""
    corpo = "\n\n".join(f"{item['texto']}\n\nFonte: {item['fonte']}" for item in fontes)
    return f"{externa or _ABERTURA_DO_FAQ}\n\n{corpo}"


def _com_tom_da_venus(texto: str, estado: EstadoVenus) -> str:
    """Reescreve no tom da Venus o texto já montado das fontes. Se a reescrita não
    passar pelas conferências, vai o texto original: nenhum fato vem só do LLM."""
    if (
        os.getenv("VENUS_TOM_LLM", "0") != "1"
        or not texto
        or len(texto) > _TAMANHO_MAXIMO_PARA_TOM
        or "Atenção:" in texto
        or estado.get("rota") == "faq"
        or estado.get("agendamento_pendente")
    ):
        return texto
    pergunta = estado.get("pergunta_original") or estado.get("mensagem_usuario") or ""
    mensagens = [("system", TOM_PROMPT), ("human", f"PERGUNTA={pergunta}\n\nTEXTO_BASE:\n{texto}")]
    try:
        reescrita = extrair_texto_resposta(get_llm_orquestrador().invoke(mensagens)).strip()
    except Exception:
        logger.exception("Falha ao reescrever no tom da Venus; usando o texto original")
        return texto
    if not _reescrita_fiel(texto, reescrita, estado) or not conferir_reescrita(texto, reescrita):
        logger.info("Reescrita de tom reprovada; usando o texto original")
        return texto
    return _sem_ids_internos(reescrita)


def _reescrita_fiel(original: str, reescrita: str, estado: EstadoVenus) -> bool:
    """Conferência sem LLM: mesmos números, frases fixas e nomes de produtos."""
    if not reescrita or _numeros(original) != _numeros(reescrita):
        return False
    fixas = [linha for linha in original.splitlines() if "alergia" in linha.lower()]
    fixas += [frase for frase in _FRASES_FIXAS if frase in original]
    if not all(frase in reescrita for frase in fixas):
        return False
    compacta = _compactar(reescrita)
    original_compacto = _compactar(original)
    produtos = dados_da_evidencia(estado.get("evidencias_tools"), "search_product")
    nomes = [str(p["name"]) for p in produtos if isinstance(p, dict) and p.get("name")] if isinstance(produtos, list) else []
    nomes += _itens_obrigatorios(estado.get("evidencias_tools"), estado.get("pergunta_original") or "")
    return all(_compactar(nome) in compacta for nome in nomes if _compactar(nome) in original_compacto)


def _numeros(texto: str) -> set[str]:
    """Números do texto, sem a numeração de passos ("1)")."""
    return set(re.findall(r"\d+(?:[.,]\d+)?", re.sub(r"\b\d+\)", "", texto)))


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

    if rota_so_com_tools(estado.get("rota")):
        texto = _com_tom_da_venus(_sem_ids_internos(_resposta_das_tools(estado)), estado)
        alertas = [] if "Atenção:" in texto else _alertas_de_alergia(texto, estado.get("evidencias_tools"))
        texto = "\n\n".join([texto, *alertas])
        # O "sim" confirma exatamente o que o código mostra (ver abaixo).
        if estado.get("agendamento_pendente"):
            texto = f"{texto}\n\n{_pedido_de_confirmacao(estado['agendamento_pendente'])}"
        return {"resposta_final": texto, "alertas_alergia_anexados": len(alertas)}

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
    alertas = _alertas_de_alergia(texto, estado.get("evidencias_tools"))
    if alertas:
        logger.info("Alerta de alergia anexado pelo código (%d)", len(alertas))
        texto = "\n\n".join([texto, *alertas])
    pendentes = estado.get("agendamento_pendente")
    if pendentes and _AFIRMA_AGENDAMENTO_RE.search(texto):
        logger.warning("Orquestrador afirmou um agendamento ainda não confirmado; texto substituído")
        texto = _RESPOSTA_ANTES_DA_CONFIRMACAO
        alertas = []  # foram junto com o texto trocado
    if pendentes:
        # O que será gravado é mostrado pelo código, não pelo LLM: o "sim"
        # confirma exatamente este texto.
        texto = f"{texto}\n\n{_pedido_de_confirmacao(pendentes)}"
    return {"resposta_final": texto, "alertas_alergia_anexados": len(alertas)}


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
    real, 2026-10-21, a resposta saiu com "CeraVe ... (product_id 3)"). Também
    troca a oferta de salvar produto nos favoritos/listas, que a IA não faz."""
    texto = _ID_INTERNO_RE.sub("", texto or "").strip()
    if _OFERTA_DE_SALVAR_RE.search(texto):
        logger.warning("Texto oferecia salvar produto nos favoritos/listas; frase trocada")
        texto = _OFERTA_DE_SALVAR_RE.sub(_ORIENTACAO_SALVAR_NO_APP, texto, count=1)
        texto = _OFERTA_DE_SALVAR_RE.sub("", texto).strip()
    return texto


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
