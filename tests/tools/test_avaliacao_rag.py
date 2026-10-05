"""O script manual de avaliação do RAG (`tests/manual/avaliar_rag.py`) roda
com a pasta do FAQ e o tipo de embedding escolhidos — inclusive o
`EmbeddingsHash`, que é o que a produção usa quando o FastEmbed não está
disponível — e o conjunto de perguntas cobre as da revisão técnica 2."""

import importlib.util
import json
from pathlib import Path

import pytest

from venus_sdk.config.settings import FAQ_DIR

RAIZ = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def avaliar_rag():
    spec = importlib.util.spec_from_file_location("avaliar_rag", RAIZ / "tests" / "manual" / "avaliar_rag.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_perguntas_da_revisao_estao_no_conjunto():
    perguntas = {
        item["pergunta"]: item["fontes"]
        for item in map(json.loads, (RAIZ / "tests/fixtures/avaliacao_rag.jsonl").read_text(encoding="utf-8").splitlines())
    }
    assert "privacidade_e_dados.md" in perguntas["vocês vendem meus dados?"]
    assert "alergias_e_limites.md" in perguntas["posso confiar na IA se tenho alergia?"]
    # O FAQ não diz quem criou o app: o certo é "não sei".
    assert perguntas["quem criou o aplicativo?"] == []


def test_roda_com_hash_e_pasta_escolhida(avaliar_rag, capsys):
    assert avaliar_rag.main(["--faq-dir", str(FAQ_DIR), "--embeddings", "hash"]) == 0
    saida = capsys.readouterr().out
    assert "embeddings: hash" in saida and str(FAQ_DIR) in saida
    assert "hit@3" in saida


def test_metricas_com_hash(avaliar_rag):
    indice = avaliar_rag.criar_indice("local", "hash", Path(FAQ_DIR))
    metricas = avaliar_rag.avaliar(indice, avaliar_rag.carregar_perguntas(), corte=0.1)
    assert set(metricas) == {"hit@3", "mrr", "nao_sei_correto"}
    assert all(0 <= valor <= 1 for valor in metricas.values())


def test_hash_so_existe_no_indice_local(avaliar_rag):
    with pytest.raises(SystemExit):
        avaliar_rag.main(["--indice", "qdrant", "--embeddings", "hash"])


# --- Revisão técnica 3, item 6: avaliação reproduzível no CI ------------------


def test_falha_quando_o_hit3_fica_abaixo_do_minimo(avaliar_rag, capsys):
    assert avaliar_rag.main(["--embeddings", "hash", "--minimo-hit3", "0.99"]) == 1
    assert "abaixo do mínimo" in capsys.readouterr().out


def test_passa_quando_o_hit3_atinge_o_minimo(avaliar_rag):
    assert avaliar_rag.main(["--embeddings", "hash", "--minimo-hit3", "0.5"]) == 0


def test_grava_a_tabela_em_markdown(avaliar_rag, tmp_path):
    arquivo = tmp_path / "tabela.md"
    avaliar_rag.main(["--embeddings", "hash", "--saida-markdown", str(arquivo)])
    texto = arquivo.read_text(encoding="utf-8")
    assert "| Corte | hit@3 | MRR |" in texto and "local + hash" in texto
    # O corte de produção aparece marcado.
    assert "**0.10**" in texto


def test_workflow_de_avaliacao_do_rag():
    workflow = (RAIZ / ".github/workflows/avaliar-rag.yaml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in workflow
    for caminho in ("src/venus_sdk/data/faq/**", "src/venus_sdk/rag/**", "tests/fixtures/avaliacao_rag.jsonl"):
        assert caminho in workflow
    assert "actions/cache" in workflow and "FASTEMBED_CACHE_PATH" in workflow
    assert "--minimo-hit3 0.8" in workflow and "GITHUB_STEP_SUMMARY" in workflow
    assert "--embeddings hash" in workflow and "upload-artifact" in workflow
