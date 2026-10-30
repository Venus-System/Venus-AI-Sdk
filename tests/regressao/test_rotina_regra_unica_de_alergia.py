"""`suggest_routine` tinha uma cópia própria do casamento alergia × ingrediente,
que já tinha divergido de `venus_sdk/alergias.py` (Juiz e Orquestrador): excluía
"benzyl alcohol" de quem declarou alergia a álcool e não pegava salicilatos.
Agora a rotina usa a mesma regra (ver `docs/avaliacao-alergias.md`)."""

from venus_sdk.tools.rotina import _montar_rotina, _termos_da_alergia


def _favorito(product_id: int, nome: str, inci: list[str]) -> dict:
    return {"product_id": product_id, "name": nome, "category_name": "Hidratante", "inci_names": inci}


def _excluidos(favoritos: list[dict], alergia: str) -> list[int]:
    rotina = _montar_rotina(favoritos, _termos_da_alergia(alergia), "noite")
    return [produto["product_id"] for produto in rotina["excluidos_por_alergia"]]


def test_alcool_benzilico_nao_e_o_alcool_da_alergia() -> None:
    favoritos = [_favorito(1, "Creme A", ["benzyl alcohol", "glycerin"]), _favorito(2, "Tônico B", ["alcohol denat."])]
    assert _excluidos(favoritos, "Álcool") == [2]


def test_salicilato_entra_na_alergia_a_salicilatos() -> None:
    favoritos = [_favorito(1, "Creme A", ["sodium salicylate"]), _favorito(2, "Creme B", ["glycerin"])]
    assert _excluidos(favoritos, "Ácido Salicílico (salicilatos)") == [1]


def test_oleaginosa_entra_na_alergia_a_nozes() -> None:
    favoritos = [_favorito(1, "Óleo A", ["prunus amygdalus dulcis oil"]), _favorito(2, "Óleo B", ["cocos nucifera oil"])]
    assert _excluidos(favoritos, "Nozes / Karité") == [1]
