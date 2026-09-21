"""Tools do agente Rotina — perfil, favoritos e listas do usuário (Postgres,
schema `venus`), mais uma sugestão de rotina montada SÓ com dado real."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool, tool

from venus_sdk.tools._util import consultar, executar, nao_encontrado

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
_SO_NOITE = ("tratamento", "retinol", "acido", "renovador", "noite", "night")
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


def _sem_acento_py(texto: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


def _ordem_categoria(categoria: str) -> int:
    cat = _sem_acento_py(categoria)
    for palavra, ordem in _ORDEM_PALAVRAS:
        if palavra in cat:
            return ordem
    return 99


def _termos_da_alergia(nome: str) -> list[str]:
    """'Fragrância (Parfum)' -> ['fragrancia (parfum)', 'fragrancia', 'parfum', + sinônimos]."""
    base = _sem_acento_py(nome)
    termos = {base}
    termos.update(t.strip(" )") for t in base.replace("(", "/").split("/") if t.strip())
    for chave, sins in _SINONIMOS_ALERGIA.items():
        if chave in base:
            termos.update(sins)
    return sorted(t for t in termos if len(t) >= 3)


def montar_tools_rotina(pool: Any) -> list[BaseTool]:
    """Monta as tools do agente Rotina, com o `pool` capturado por closure.
    Levanta `ValueError` se `pool` for `None` (só no primeiro uso real)."""
    if pool is None:
        raise ValueError(
            "montar_tools_rotina requer um pool do Postgres (asyncpg) — "
            "quem monta o grafo deve criar o pool e passar via "
            "compilar_grafo_venus(pool=...)."
        )

    async def _favoritos_com_ingredientes(user_id: int) -> Any:
        query = """
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
        return await consultar(pool, "suggest_routine", query, user_id,
                               vazio="o usuário não tem produtos favoritos")

    @tool
    async def get_user_profile(user_id: int) -> dict:
        """Traz o perfil do usuário (tipo de pele/cabelo, sensibilidade, condições como acne/melasma/gestação e tags de perfil)
        — base para montar ou ajustar uma rotina."""
        # `to_jsonb(up)` em vez de colunas fixas: o schema do perfil evolui (a coluna
        # `hair_type` já sumiu do banco real) e uma coluna a menos não pode derrubar a tool.
        query = """
            SELECT (to_jsonb(up) - 'user_profile_id' - 'fk_user_id' - 'is_active' - 'created_at'
                    - 'updated_at')::text AS perfil,
                   COALESCE((SELECT array_agg(pt.name ORDER BY pt.name)
                             FROM venus.user_profile_tags upt
                             JOIN venus.profile_tags pt ON pt.profile_tag_id = upt.fk_profile_tag_id
                             WHERE upt.fk_user_id = up.fk_user_id AND upt.is_active), '{}') AS tags
            FROM venus.user_profiles up
            WHERE up.fk_user_id = $1 AND up.is_active
        """
        r = await consultar(pool, "get_user_profile", query, user_id, uma_linha=True,
                            vazio="o usuário não tem perfil cadastrado")
        if isinstance(r, dict) and "perfil" in r:
            return {**json.loads(r["perfil"]), "tags": r["tags"]}
        return r

    @tool
    async def get_user_favorites(user_id: int) -> list[dict] | dict:
        """Lista os produtos favoritados pelo usuário (id, nome, marca,
        categoria). A rotina só pode usar produtos daqui ou das listas."""
        query = """
            SELECT p.product_id, p.name, b.name AS brand_name, pc.name AS category_name
            FROM venus.favorites f
            JOIN venus.products p ON p.product_id = f.fk_product_id
            JOIN venus.brands b ON b.brand_id = p.fk_brand_id
            JOIN venus.product_categories pc ON pc.product_category_id = p.fk_product_category_id
            WHERE f.fk_user_id = $1
            ORDER BY f.created_at, p.name
        """
        return await consultar(pool, "get_user_favorites", query, user_id,
                               vazio="o usuário não tem produtos favoritos")

    @tool
    async def get_user_lists(user_id: int) -> list[dict] | dict:
        """Lista as listas de produtos do usuário e os produtos de cada uma."""
        query = """
            SELECT ul.user_list_id, ul.name AS list_name, p.product_id, p.name AS product_name
            FROM venus.user_lists ul
            LEFT JOIN venus.user_list_items uli ON uli.fk_user_list_id = ul.user_list_id
            LEFT JOIN venus.products p ON p.product_id = uli.fk_product_id
            WHERE ul.fk_user_id = $1
            ORDER BY ul.user_list_id, p.name
        """
        return await consultar(pool, "get_user_lists", query, user_id,
                               vazio="o usuário não tem listas")

    @tool
    async def add_favorite(user_id: int, product_id: int) -> dict:
        """Adiciona um produto aos favoritos do usuário (idempotente)."""
        query = """
            INSERT INTO venus.favorites (fk_user_id, fk_product_id)
            SELECT $1, p.product_id FROM venus.products p WHERE p.product_id = $2
            ON CONFLICT (fk_user_id, fk_product_id) DO NOTHING
        """
        existe = await consultar(pool, "add_favorite", "SELECT 1 AS ok FROM venus.products WHERE product_id = $1",
                                 product_id, uma_linha=True, vazio="produto não encontrado")
        if existe.get("encontrado") is False or "erro" in existe:
            return existe
        resultado = await executar(pool, "add_favorite", query, user_id, product_id)
        if isinstance(resultado, dict):
            return resultado
        return {"ok": True, "mensagem": "produto adicionado aos favoritos"}

    @tool
    async def remove_favorite(user_id: int, product_id: int) -> dict:
        """Remove um produto dos favoritos do usuário."""
        resultado = await executar(
            pool, "remove_favorite",
            "DELETE FROM venus.favorites WHERE fk_user_id = $1 AND fk_product_id = $2",
            user_id, product_id,
        )
        if isinstance(resultado, dict):
            return resultado
        removidos = int(str(resultado).split()[-1]) if str(resultado).split() else 0
        if removidos == 0:
            return nao_encontrado("esse produto não estava nos favoritos do usuário")
        return {"ok": True, "mensagem": "produto removido dos favoritos"}

    @tool
    async def suggest_routine(user_id: int, horario: str = "ambos") -> dict:
        """Monta uma rotina (`horario`: 'manha', 'noite' ou 'ambos') usando
        APENAS os favoritos do usuário, ordenados por boas práticas por
        categoria, EXCLUINDO produtos com ingrediente que bata com as
        alergias declaradas. Devolve também os produtos excluídos e as
        categorias sem produto (para o especialista pedir esclarecimento em
        vez de inventar)."""
        horario = (horario or "ambos").lower().replace("ã", "a")
        if horario not in {"manha", "noite", "ambos"}:
            return {"erro": "horario deve ser 'manha', 'noite' ou 'ambos'"}
        favoritos = await _favoritos_com_ingredientes(user_id)
        if isinstance(favoritos, dict):
            return favoritos
        alergias = await consultar(
            pool, "suggest_routine",
            """SELECT lower(a.allergy_name) AS nome FROM venus.user_allergies ua
               JOIN venus.allergies a ON a.allergy_id = ua.fk_allergy_id
               WHERE ua.fk_user_id = $1""", user_id, vazio="sem alergias")
        termos: list[str] = []
        if isinstance(alergias, list):
            for linha in alergias:
                termos += _termos_da_alergia(linha["nome"])
        excluidos, candidatos = [], []
        for f in favoritos:
            bate = [t for t in termos if any(t in _sem_acento_py(i) for i in f["inci_names"])]
            (excluidos if bate else candidatos).append({**f, "motivo": bate})
        passos = []
        for f in candidatos:
            rotulo = _sem_acento_py(f["category_name"] + " " + f["name"])
            if horario == "manha" and any(k in rotulo for k in _SO_NOITE):
                continue
            if horario == "noite" and any(k in rotulo for k in _SO_MANHA):
                continue
            passos.append(f)
        passos.sort(key=lambda f: _ordem_categoria(f["category_name"]))
        return {
            "horario": horario,
            "passos": [
                {"ordem": n, "product_id": f["product_id"], "nome": f["name"],
                 "categoria": f["category_name"]}
                for n, f in enumerate(passos, 1)
            ],
            "excluidos_por_alergia": [
                {"product_id": f["product_id"], "nome": f["name"], "ingredientes_em_conflito": f["motivo"]}
                for f in excluidos
            ],
            "sem_produto_para": [
                nome for nome, palavras in (("Limpeza", ("limp",)), ("Hidratante", ("hidrat", "creme")),
                                            ("Protetor solar", ("protetor", "solar", "fps")))
                if not any(p_ in _sem_acento_py(x["category_name"] + " " + x["name"]) for x in passos for p_ in palavras)
                and not (nome == "Protetor solar" and horario == "noite")
            ],
        }

    return [get_user_profile, get_user_favorites, get_user_lists, add_favorite,
            remove_favorite, suggest_routine]
