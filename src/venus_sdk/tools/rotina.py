"""Tools do agente Rotina — perfil, favoritos e listas do usuário (Postgres,
schema `venus`), mais uma sugestão de rotina montada SÓ com dado real."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool, tool

from venus_sdk.texto import remover_acentos
from venus_sdk.tools._identidade import SEM_USUARIO_IDENTIFICADO, resolver_user_id
from venus_sdk.tools._util import consultar, exigir_pool

# Ordem de boas práticas por PALAVRA-CHAVE na categoria (limpeza antes de
# tratamento, hidratante antes de protetor solar). O catálogo real tem ~120
# categorias ("Gel de Limpeza", "Sérum Facial", "Cabelo - Shampoo"...), então
# o casamento é por substring, na 1ª palavra-chave que aparecer.
_ORDEM_PALAVRAS = [
    ("limp", 1), ("demaquil", 1), ("balsamo", 1), ("shampoo", 1), ("micelar", 1),
    ("esfol", 2), ("tonic", 2), ("condicionador", 2), ("mascara", 3),
    ("serum", 3), ("ampola", 3), ("tratamento", 4), ("acne", 4), ("olhos", 4),
    ("hidrat", 5), ("creme", 5), ("loc", 5), ("oleo", 5), ("finaliz", 5), ("leave", 5),
    ("protetor", 6), ("solar", 6), ("fps", 6),
]
# Categoria sem nenhuma palavra-chave acima vai para o fim da rotina.
_ORDEM_DESCONHECIDA = 99
_SO_NOITE = (
    "tratamento", "retinol", "retinal", "tretinoina", "renovador", "noite", "night", "peeling",
    "acido glicolico", "acido salicilico", "acido lactico", "acido mandelico", "aha", "bha",
)
_SO_MANHA = ("protetor", "solar", "fps", "spf", "dia ", "day")

# Alergias no catálogo vêm em português/inglês; os ingredientes vêm como INCI.
_SINONIMOS_ALERGIA = {
    "fragrancia": ("parfum", "fragrance", "aroma", "perfume", "fragrancia"),
    "perfume": ("parfum", "fragrance", "aroma", "perfume"),
    "conservante": ("paraben", "phenoxyethanol", "methylisothiazolinone"),
    "paraben": ("paraben",),
    "sulfato": ("sulfate", "sulfato"),
    "silicone": ("dimethicone", "siloxane", "silicone"),
    "alcool": ("alcohol denat", "alcohol"),
}
# Álcoois graxos (emolientes) não são o "álcool" das alergias/sensibilidades.
_ALCOOIS_GRAXOS = (
    "cetyl", "stearyl", "cetearyl", "behenyl", "myristyl", "lauryl alcohol", "oleyl", "arachidyl",
    "cetilico", "estearilico", "cetoestearilico", "behenilico", "miristilico", "oleilico",
)
_TERMOS_DE_ALCOOL = ("alcool", "alcohol")


# Categorias essenciais de uma rotina e as palavras-chave que as identificam.
_CATEGORIAS_ESSENCIAIS = (
    ("Limpeza", ("limp",)),
    ("Hidratante", ("hidrat", "creme")),
    ("Protetor solar", ("protetor", "solar", "fps")),
)
_HORARIOS_VALIDOS = {"manha", "noite", "ambos"}
_SEM_FAVORITOS = "o usuário não tem produtos favoritos"
_TAMANHO_MINIMO_TERMO_ALERGIA = 3


def _minusculo_sem_acento(texto: str) -> str:
    return remover_acentos(texto.lower())


def _ordem_categoria(categoria: str) -> int:
    categoria_normalizada = _minusculo_sem_acento(categoria)
    for palavra, ordem in _ORDEM_PALAVRAS:
        if palavra in categoria_normalizada:
            return ordem
    return _ORDEM_DESCONHECIDA


def _termos_da_alergia(nome: str) -> list[str]:
    """'Fragrância (Parfum)' -> ['fragrancia (parfum)', 'fragrancia', 'parfum', + sinônimos]."""
    base = _minusculo_sem_acento(nome)
    termos = {base}
    termos.update(parte.strip(" )") for parte in base.replace("(", "/").split("/") if parte.strip())
    for chave, sinonimos in _SINONIMOS_ALERGIA.items():
        if chave in base:
            termos.update(sinonimos)
    return sorted(termo for termo in termos if len(termo) >= _TAMANHO_MINIMO_TERMO_ALERGIA)


def _rotulo(produto: dict) -> str:
    return _minusculo_sem_acento(produto["category_name"] + " " + produto["name"])


def _bate_com_alergia(termo: str, ingrediente: str) -> bool:
    if termo not in ingrediente:
        return False
    eh_termo_de_alcool = any(alcool in termo for alcool in _TERMOS_DE_ALCOOL)
    return not (eh_termo_de_alcool and any(graxo in ingrediente for graxo in _ALCOOIS_GRAXOS))


def _separar_por_alergia(favoritos: list[dict], termos: list[str]) -> tuple[list[dict], list[dict]]:
    """`(excluidos, candidatos)`; cada item ganha `motivo` = termos de alergia que bateram."""
    excluidos: list[dict] = []
    candidatos: list[dict] = []
    for produto in favoritos:
        ingredientes = [_minusculo_sem_acento(ingrediente) for ingrediente in produto["inci_names"]]
        em_conflito = [
            termo for termo in termos if any(_bate_com_alergia(termo, ingrediente) for ingrediente in ingredientes)
        ]
        (excluidos if em_conflito else candidatos).append({**produto, "motivo": em_conflito})
    return excluidos, candidatos


def _serve_no_horario(produto: dict, horario: str) -> bool:
    rotulo = _rotulo(produto)
    if horario == "manha" and any(palavra in rotulo for palavra in _SO_NOITE):
        return False
    if horario == "noite" and any(palavra in rotulo for palavra in _SO_MANHA):
        return False
    return True


def _categorias_sem_produto(passos: list[dict], horario: str) -> list[str]:
    rotulos = [_rotulo(produto) for produto in passos]
    faltando = []
    for nome, palavras in _CATEGORIAS_ESSENCIAIS:
        if nome == "Protetor solar" and horario == "noite":
            continue
        if not any(palavra in rotulo for rotulo in rotulos for palavra in palavras):
            faltando.append(nome)
    return faltando


def _montar_rotina(favoritos: list[dict], termos_alergia: list[str], horario: str) -> dict:
    excluidos, candidatos = _separar_por_alergia(favoritos, termos_alergia)
    passos = [produto for produto in candidatos if _serve_no_horario(produto, horario)]
    passos.sort(key=lambda produto: _ordem_categoria(produto["category_name"]))
    return {
        "horario": horario,
        "passos": [
            {"ordem": ordem, "product_id": produto["product_id"], "nome": produto["name"],
             "categoria": produto["category_name"]}
            for ordem, produto in enumerate(passos, 1)
        ],
        "excluidos_por_alergia": [
            {"product_id": produto["product_id"], "nome": produto["name"],
             "ingredientes_em_conflito": produto["motivo"]}
            for produto in excluidos
        ],
        "sem_produto_para": _categorias_sem_produto(passos, horario),
    }


_SQL_FAVORITOS_COM_INGREDIENTES = """
    SELECT p.product_id, p.name, pc.name AS category_name,
           COALESCE(array_agg(DISTINCT lower(i.inci_name))
                    FILTER (WHERE i.inci_name IS NOT NULL), '{}')
           || COALESCE(array_agg(DISTINCT lower(i.common_name))
                    FILTER (WHERE i.common_name IS NOT NULL), '{}') AS inci_names
    FROM venus.favorites f
    JOIN venus.products p ON p.product_id = f.fk_product_id
    JOIN venus.product_categories pc ON pc.product_category_id = p.fk_product_category_id
    LEFT JOIN venus.product_versions pv ON pv.fk_product_id = p.product_id AND pv.is_current
    LEFT JOIN venus.product_ingredients pi ON pi.fk_product_version_id = pv.product_version_id
    LEFT JOIN venus.ingredients i ON i.ingredient_id = pi.fk_ingredient_id
    WHERE f.fk_user_id = $1
    GROUP BY p.product_id, p.name, pc.name
    ORDER BY p.name
"""


_SQL_PERFIL_DO_USUARIO = """
    SELECT (to_jsonb(up) - 'user_profile_id' - 'fk_user_id' - 'is_active' - 'created_at'
            - 'updated_at')::text AS perfil,
           COALESCE((SELECT array_agg(pt.name ORDER BY pt.name)
                     FROM venus.user_profile_tags upt
                     JOIN venus.profile_tags pt ON pt.profile_tag_id = upt.fk_profile_tag_id
                     WHERE upt.fk_user_id = up.fk_user_id AND upt.is_active), '{}') AS tags
    FROM venus.user_profiles up
    WHERE up.fk_user_id = $1 AND up.is_active
"""


_SQL_FAVORITOS_DO_USUARIO = """
    SELECT p.product_id, p.name, b.name AS brand_name, pc.name AS category_name
    FROM venus.favorites f
    JOIN venus.products p ON p.product_id = f.fk_product_id
    JOIN venus.brands b ON b.brand_id = p.fk_brand_id
    JOIN venus.product_categories pc ON pc.product_category_id = p.fk_product_category_id
    WHERE f.fk_user_id = $1
    ORDER BY f.created_at, p.name
"""


_SQL_LISTAS_DO_USUARIO = """
    SELECT ul.user_list_id, ul.name AS list_name, p.product_id, p.name AS product_name
    FROM venus.user_lists ul
    LEFT JOIN venus.user_list_items uli ON uli.fk_user_list_id = ul.user_list_id
    LEFT JOIN venus.products p ON p.product_id = uli.fk_product_id
    WHERE ul.fk_user_id = $1
    ORDER BY ul.user_list_id, p.name
"""


_SQL_ALERGIAS_PARA_ROTINA = """SELECT lower(a.allergy_name) AS nome FROM venus.user_allergies ua
               JOIN venus.allergies a ON a.allergy_id = ua.fk_allergy_id
               WHERE ua.fk_user_id = $1"""


async def montar_rotina_do_usuario(pool: Any, user_id: int, horario: str) -> dict:
    """A rotina do usuário para `horario` ('manha', 'noite' ou 'ambos'): só
    favoritos, sem o que bate com alergia, em ordem de boas práticas. Devolve
    o dict de `suggest_routine` ou um `{"erro"|"encontrado": ...}`."""
    favoritos = await consultar(pool, "suggest_routine", _SQL_FAVORITOS_COM_INGREDIENTES, user_id,
                                vazio=_SEM_FAVORITOS)
    if isinstance(favoritos, dict):
        return favoritos
    alergias = await consultar(pool, "suggest_routine", _SQL_ALERGIAS_PARA_ROTINA, user_id, vazio="sem alergias")
    if isinstance(alergias, dict) and "erro" in alergias:
        return {**alergias, "mensagem": "não consegui checar as alergias; a rotina não foi montada"}
    termos: list[str] = []
    if isinstance(alergias, list):
        for linha in alergias:
            termos += _termos_da_alergia(linha["nome"])
    return _montar_rotina(favoritos, termos, horario)


def montar_tools_rotina(pool: Any) -> list[BaseTool]:
    """Monta as tools do agente Rotina, com o `pool` capturado por closure.
    Levanta `ValueError` se `pool` for `None` (só no primeiro uso real)."""
    exigir_pool(pool, "montar_tools_rotina")

    @tool
    async def get_user_profile(user_id: int) -> dict:
        """Traz o perfil do usuário (tipo de pele/cabelo, sensibilidade, condições como acne/melasma/gestação e tags de perfil)
        — base para montar ou ajustar uma rotina."""
        # `to_jsonb(up)` em vez de colunas fixas: o schema do perfil evolui (a coluna
        # `hair_type` já sumiu do banco real) e uma coluna a menos não pode derrubar a tool.
        user_id = resolver_user_id(user_id)
        if user_id is None:
            return SEM_USUARIO_IDENTIFICADO
        resultado = await consultar(pool, "get_user_profile", _SQL_PERFIL_DO_USUARIO, user_id, uma_linha=True,
                                    vazio="o usuário não tem perfil cadastrado")
        if isinstance(resultado, dict) and "perfil" in resultado:
            return {**json.loads(resultado["perfil"]), "tags": resultado["tags"]}
        return resultado

    @tool
    async def get_user_favorites(user_id: int) -> list[dict] | dict:
        """Lista os produtos favoritados pelo usuário (id, nome, marca,
        categoria). A rotina só pode usar produtos daqui ou das listas."""
        user_id = resolver_user_id(user_id)
        if user_id is None:
            return SEM_USUARIO_IDENTIFICADO
        return await consultar(pool, "get_user_favorites", _SQL_FAVORITOS_DO_USUARIO, user_id,
                               vazio=_SEM_FAVORITOS)

    @tool
    async def get_user_lists(user_id: int) -> list[dict] | dict:
        """Lista as listas de produtos do usuário e os produtos de cada uma."""
        user_id = resolver_user_id(user_id)
        if user_id is None:
            return SEM_USUARIO_IDENTIFICADO
        return await consultar(pool, "get_user_lists", _SQL_LISTAS_DO_USUARIO, user_id,
                               vazio="o usuário não tem listas")

    @tool
    async def suggest_routine(user_id: int, horario: str = "ambos") -> dict:
        """Monta uma rotina (`horario`: 'manha', 'noite' ou 'ambos') usando
        APENAS os favoritos do usuário, ordenados por boas práticas por
        categoria, EXCLUINDO produtos com ingrediente que bata com as
        alergias declaradas. Devolve também os produtos excluídos e as
        categorias sem produto (para o especialista pedir esclarecimento em
        vez de inventar)."""
        horario = (horario or "ambos").lower().replace("ã", "a")
        if horario not in _HORARIOS_VALIDOS:
            return {"erro": "horario deve ser 'manha', 'noite' ou 'ambos'"}
        user_id = resolver_user_id(user_id)
        if user_id is None:
            return SEM_USUARIO_IDENTIFICADO
        return await montar_rotina_do_usuario(pool, user_id, horario)

    # Todas só leem. Não existe tool para adicionar nem remover favorito de
    # propósito: mexer nos favoritos é decisão do usuário, feita por ele no app.
    return [get_user_profile, get_user_favorites, get_user_lists, suggest_routine]
