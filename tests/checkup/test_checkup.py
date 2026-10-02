"""Check-up da rotina sem Neo4j: executor de Cypher falso."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from _fakes import ConexaoFalsa, PoolFalso
from venus_sdk.checkup import consultas, sincronizar
from venus_sdk.tools import checkup
from venus_sdk.tools._identidade import SEM_USUARIO_IDENTIFICADO, usuario_da_conversa

_MANHA = {"passos": [{"ordem": 1, "product_id": 1, "nome": "Limpeza"},
                     {"ordem": 2, "product_id": 2, "nome": "Sérum Vitamina C"}]}
_NOITE = {"passos": [{"ordem": 1, "product_id": 1, "nome": "Limpeza"},
                     {"ordem": 2, "product_id": 3, "nome": "Sérum Retinol"}]}


def run(coro):
    return asyncio.run(coro)


class ExecutorFalso:
    """Devolve linhas prontas por consulta e guarda o que recebeu."""

    def __init__(self, respostas: dict[str, list[dict]] | None = None, erro: Exception | None = None):
        self.respostas = respostas or {}
        self.erro = erro
        self.chamadas: list[tuple[str, dict]] = []

    async def __call__(self, consulta: str, parametros: dict) -> list[dict]:
        self.chamadas.append((consulta, parametros))
        if self.erro:
            raise self.erro
        return self.respostas.get(consulta, [])


def _tool(executor):
    return checkup.montar_tools_checkup(PoolFalso(ConexaoFalsa()), executar_cypher=executor)[0]


def _com_rotina():
    rotinas = {"manha": _MANHA, "noite": _NOITE}
    return patch.object(checkup, "montar_rotina_do_usuario", new_callable=AsyncMock,
                        side_effect=lambda pool, user_id, periodo: rotinas[periodo])


# --- parâmetros ---


def test_parametros_levam_periodo_e_ordem_que_a_venus_montou():
    p = consultas.parametros_da_rotina({"manha": _MANHA["passos"], "noite": _NOITE["passos"]})
    assert {"id": 3, "periodo": "noite", "ordem": 2} in p["produtos"]
    assert p["ids_manha"] == [1, 2] and p["ids_noite"] == [1, 3] and p["ids_todos"] == [1, 2, 3]


# --- tool ---


def test_avisos_viram_texto_estruturado_e_sao_filtrados_pelo_periodo():
    executor = ExecutorFalso({
        consultas.CONSULTA_CONFLITO: [{"periodo": "manha", "nome_a": "A", "tipo_a": "AHA", "nome_b": "B",
                                       "tipo_b": "Vitamina C", "severidade": "leve", "motivo": "irrita", "fonte": None}],
        consultas.CONSULTA_FALTANDO: [
            {"periodo": "noite", "nome": "Sérum Retinol", "tipo": "Retinoide", "precisa_de": "Filtro UV",
             "periodo_exigido": "manha", "motivo": "sol", "fonte": None},
            {"periodo": "noite", "nome": "Sérum Retinol", "tipo": "Retinoide", "precisa_de": "Filtro UV",
             "periodo_exigido": "manha", "motivo": "sol", "fonte": None},
        ],
    })
    with _com_rotina():
        r = run(_tool(executor).ainvoke({"user_id": 7, "periodo": "noite"}))
    assert r["checado"] is True
    assert r["avisos"] == [{"tipo": "faltando", "periodo": "noite", "produto": "Sérum Retinol", "ativo": "Retinoide",
                            "precisa_de": "Filtro UV", "periodo_exigido": "manha", "motivo": "sol", "fonte": None}]
    # O dia inteiro entra nas consultas, mesmo pedindo só a noite.
    assert executor.chamadas[0][1]["ids_manha"] == [1, 2]


def test_rotina_vazia_nao_consulta_o_neo4j():
    executor = ExecutorFalso()
    with patch.object(checkup, "montar_rotina_do_usuario", new_callable=AsyncMock,
                      return_value={"passos": [], "sem_produto_para": ["Limpeza"]}):
        assert run(_tool(executor).ainvoke({"user_id": 7}))["avisos"] == []
    assert executor.chamadas == []


def test_neo4j_fora_do_ar_nao_derruba_a_rotina():
    with _com_rotina():
        r = run(_tool(ExecutorFalso(erro=ConnectionError("neo4j fora"))).ainvoke({"user_id": 7}))
    assert r == checkup.NAO_CHECADO


def test_sem_neo4j_configurado_devolve_nao_checado():
    with patch.object(checkup, "neo4j_configurado", return_value=False):
        tool = checkup.montar_tools_checkup(PoolFalso(ConexaoFalsa()))[0]
        assert run(tool.ainvoke({"user_id": 7})) == checkup.NAO_CHECADO


def test_erro_ao_montar_a_rotina_e_repassado():
    with patch.object(checkup, "montar_rotina_do_usuario", new_callable=AsyncMock,
                      return_value={"erro": "falha ao consultar o banco"}):
        assert "erro" in run(_tool(ExecutorFalso()).ainvoke({"user_id": 7}))


def test_usa_o_usuario_da_conversa_e_valida_o_periodo():
    with _com_rotina() as montar, usuario_da_conversa(7):
        run(_tool(ExecutorFalso()).ainvoke({"user_id": 99}))
    assert {chamada.args[1] for chamada in montar.call_args_list} == {7}
    with usuario_da_conversa(None):
        assert run(_tool(ExecutorFalso()).ainvoke({"user_id": 99})) == SEM_USUARIO_IDENTIFICADO
    assert "erro" in run(_tool(ExecutorFalso()).ainvoke({"user_id": 7, "periodo": "tarde"}))


# --- CSVs de regras ---

_PASTA = Path(__file__).resolve().parents[2] / "data" / "checkup"


def test_csvs_do_repositorio_sao_validos():
    tipos = sincronizar.carregar_tipos(_PASTA / "tipos_de_ativo.csv")
    regras = sincronizar.carregar_regras(_PASTA / "regras.csv")
    assert tipos["GLYCOLIC ACID"] == "AHA" and tipos["ZINC OXIDE"] == "Filtro UV"
    conhecidos = set(tipos.values())
    assert all(r["tipo"] in conhecidos and r["outro_tipo"] in conhecidos for r in regras)


@pytest.mark.parametrize(("linha", "erro"), [
    ("Retinoide,combina_com,AHA,,,x,,nao", "regra deve ser"),
    (",conflita_com,AHA,,,x,,nao", "obrigatórios"),
    ("Retinoide,precisa_de,Filtro UV,tarde,,x,,nao", "periodo"),
])
def test_regra_invalida_para_a_copia(tmp_path, linha, erro):
    arquivo = tmp_path / "regras.csv"
    arquivo.write_text("tipo,regra,outro_tipo,periodo,severidade,motivo,fonte,revisado\n" + linha + "\n",
                       encoding="utf-8")
    with pytest.raises(ValueError, match=erro):
        sincronizar.carregar_regras(arquivo)


def test_tipo_do_ingrediente_vem_do_csv_e_da_categoria_do_banco():
    ingredientes = [{"id": 1, "inci": "Glycolic Acid"}, {"id": 2, "inci": "AQUA"}, {"id": 3, "inci": "AVOBENZONE"}]
    ligacoes = sincronizar.tipos_dos_ingredientes(ingredientes, {"GLYCOLIC ACID": "AHA"}, [{"id": 3}])
    assert ligacoes == [{"ingrediente": 1, "tipo": "AHA"}, {"ingrediente": 3, "tipo": "Filtro UV"}]


def test_copia_grava_em_lotes_e_apaga_o_que_ficou_antigo():
    def fetch(query, *args):
        if "FROM venus.products" in query:
            return [{"id": 1, "nome": "Sérum", "categoria": "Sérum Facial"}]
        if "ingredient_categories" in query:
            raise RuntimeError("schema local sem categorias")
        if "FROM venus.ingredients" in query:
            return [{"id": 10, "inci": "RETINOL", "nome": "Retinol"}]
        if "product_ingredients" in query:
            return [{"produto": 1, "ingrediente": 10, "posicao": 3}]
        return [{"usuario": 7, "produto": 1}]

    executor = ExecutorFalso()
    contagem = run(sincronizar.sincronizar(PoolFalso(ConexaoFalsa(fetch=fetch)), executor, _PASTA))
    assert contagem["ingredientes_com_tipo"] == 1 and contagem["favoritos"] == 1
    consultas_executadas = [consulta for consulta, _ in executor.chamadas]
    assert consultas_executadas[-2:] == [sincronizar._APAGAR_RELACOES_ANTIGAS, sincronizar._APAGAR_NOS_ANTIGOS]
    rodadas = {p.get("rodada") for _, p in executor.chamadas if p.get("rodada")}
    assert len(rodadas) == 1  # tudo da mesma rodada
