"""Casamento entre as alergias declaradas pelo usuário e os ingredientes de um
produto — usado pela resposta de fallback do orquestrador
(`nodes/orquestrador.py::_parte_alergias`). Segue a mesma regra que
`suggest_routine` aplica em `tools/rotina.py` (álcool graxo não é o "álcool"
da alergia).

As alergias do catálogo vêm em português/inglês ("Fragrância (Parfum)",
"Álcool"); os ingredientes vêm como INCI + nome comum em português."""

from __future__ import annotations

import re

from venus_sdk.texto import remover_acentos

# Alérgenos de fragrância que a UE manda declarar pelo nome no INCI: quem tem
# alergia a fragrância reage a eles mesmo sem "parfum" no rótulo. "Cinnamal"
# cobre amyl e hexyl cinnamal. Fica de fora o benzyl alcohol, que também está na
# lista mas é usado sobretudo como conservante.
_ALERGENOS_DE_FRAGRANCIA = (
    "limonene", "limoneno", "linalool", "linalol", "citronellol", "citronelol", "geraniol", "eugenol",
    "coumarin", "cumarina", "citral", "cinnamal", "cinnamyl alcohol", "farnesol", "hydroxycitronellal",
    "anise alcohol", "benzyl benzoate", "benzyl cinnamate", "benzyl salicylate", "butylphenyl methylpropional",
    "alpha-isomethyl ionone", "evernia prunastri", "evernia furfuracea", "methyl 2-octynoate",
)
# Oleaginosas (tree nuts) pelo nome comum e pelo gênero do INCI. Sem "castanha"
# (pegaria a castanha-da-índia, que não é oleaginosa) nem "caju" (está dentro de
# "cajuput").
_OLEAGINOSAS = (
    "nozes", "juglans", "walnut", "amendoa", "almond", "amygdalus", "macadamia", "avela", "hazelnut",
    "corylus", "bertholletia", "brazil nut", "cashew", "anacardium", "pecan", "carya", "pistache",
    "pistachio", "pistacia",
)

_SINONIMOS_ALERGIA = {
    "fragrancia": ("parfum", "fragrance", "aroma", "perfume", "fragrancia", *_ALERGENOS_DE_FRAGRANCIA),
    "perfume": ("parfum", "fragrance", "aroma", "perfume", *_ALERGENOS_DE_FRAGRANCIA),
    "conservante": ("paraben", "phenoxyethanol", "methylisothiazolinone"),
    "paraben": ("paraben",),
    "sulfato": ("sulfate", "sulfato"),
    "silicone": ("dimethicone", "siloxane", "silicone"),
    # "Ácido Salicílico (salicilatos)": o plural não casava com "salicilato de sódio".
    "salicil": ("salicylic acid", "acido salicilico", "salicylate", "salicilato"),
    "nozes": _OLEAGINOSAS,
    "karite": ("karite", "shea", "butyrospermum"),
}

# Quem evita sulfato evita o surfactante (lauryl/laureth sulfate), não o sal
# mineral: "magnesium sulfate"/"sulfato de magnésio" é nome de duas palavras, o
# surfactante sempre tem a cadeia graxa no meio ("sodium lauryl sulfate").
_TERMOS_DE_SULFATO = {"sulfate", "sulfato"}
_SAL_DE_SULFATO_RE = re.compile(r"^(\S+ sulfate|sulfato de \S+)$")

# Alergia a "Álcool" = álcool etílico/desnaturado/isopropílico (INCI "ALCOHOL",
# "ALCOHOL DENAT.", "ETHANOL"...). Álcoois GRAXOS (CETEARYL/CETYL/STEARYL
# ALCOHOL — "álcool cetearílico") são emolientes, não esse álcool: o casamento
# por substring "alcohol"/"alcool" excluía da rotina todo creme CeraVe do
# usuário e o mandava cadastrar produtos "sem álcool" que também eram
# excluídos (conversa real de 2026-10-20). Casamento exato por ingrediente,
# nunca substring ("etanol" está dentro de "fenoxietanol").
TERMO_ALCOOL_ETILICO = "alcool etilico/desnaturado"
_ALCOOL_ETILICO_RE = re.compile(
    r"^(alcohol|alcool|ethanol|etanol|ethyl alcohol|alcool etilico|isopropyl alcohol|alcool isopropilico)$"
    r"|^(alcohol|alcool) (denat|desnaturado)|^sd alcohol"
)
_TERMOS_GENERICOS_DE_ALCOOL = {"alcool", "alcohol"}
_TAMANHO_MINIMO_TERMO_ALERGIA = 3


def _normalizar(texto: str) -> str:
    return remover_acentos(str(texto or "").lower()).strip()


def termos_da_alergia(nome: str) -> list[str]:
    """'Fragrância (Parfum)' -> ['fragrancia (parfum)', 'fragrancia', 'parfum', + sinônimos].
    'Álcool' -> [TERMO_ALCOOL_ETILICO] (ver `_ALCOOL_ETILICO_RE`)."""
    base = _normalizar(nome)
    termos = {base}
    termos.update(parte.strip(" )") for parte in base.replace("(", "/").split("/") if parte.strip())
    for chave, sinonimos in _SINONIMOS_ALERGIA.items():
        if chave in base:
            termos.update(sinonimos)
    if "alcool" in base or "alcohol" in base:
        termos -= _TERMOS_GENERICOS_DE_ALCOOL
        termos.add(TERMO_ALCOOL_ETILICO)
    return sorted(termo for termo in termos if len(termo) >= _TAMANHO_MINIMO_TERMO_ALERGIA)


def termo_bate(termo: str, ingrediente: str) -> bool:
    """True se o ingrediente (INCI ou nome comum) corresponde ao termo de alergia."""
    nome = _normalizar(ingrediente).rstrip(".")
    if termo == TERMO_ALCOOL_ETILICO:
        return bool(_ALCOOL_ETILICO_RE.search(nome))
    if termo in _TERMOS_DE_SULFATO:
        return termo in nome and not _SAL_DE_SULFATO_RE.search(nome)
    return termo in nome


def termos_em_conflito(termos: list[str], ingredientes: list[str]) -> list[str]:
    """Termos de alergia que batem com pelo menos um dos ingredientes."""
    return [termo for termo in termos if any(termo_bate(termo, ingrediente) for ingrediente in ingredientes)]
