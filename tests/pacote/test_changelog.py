"""Item 9 da revisão técnica 3: o CHANGELOG diz o que quem usa o SDK precisa
mudar a cada versão, e a PR que mexe na API pública precisa registrar isso."""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SUBSECOES = ("### Mudanças incompatíveis", "### Novidades", "### Correções")


def _secao(versao: str) -> str:
    texto = (RAIZ / "CHANGELOG.md").read_text(encoding="utf-8")
    inicio = texto.index(f"## {versao}\n")
    proxima = re.search(r"^## \d", texto[inicio + 1:], re.M)
    return texto[inicio: inicio + 1 + proxima.start()] if proxima else texto[inicio:]


def test_versoes_com_as_tres_subsecoes():
    for versao in ("0.2.0", "0.3.0"):
        secao = _secao(versao)
        assert all(subsecao in secao for subsecao in SUBSECOES), versao


def test_mudancas_incompativeis_tem_antes_e_depois():
    for versao in ("0.2.0", "0.3.0"):
        secao = _secao(versao)
        incompativeis = secao.split("### Mudanças incompatíveis", 1)[1].split("### Novidades", 1)[0]
        itens = [linha for linha in incompativeis.splitlines() if linha.startswith("- ")]
        assert itens, versao
        assert all("**Antes:**" in item or "Nenhuma mudança incompatível" in item for item in
                   re.split(r"\n(?=- )", incompativeis.strip()) if item.startswith("- "))


def test_0_3_0_cobre_as_mudancas_de_comportamento_da_revisao_3():
    secao = _secao("0.3.0")
    for termo in ("VENUS_FASTEMBED_DOWNLOAD", "VENUS_GUARDRAIL_LLM_FALHAS_PARA_ABRIR", "get_llm_guardrail",
                  "venus_sdk.checkup.sincronizar", "pronto", "calculos"):
        assert termo in secao, termo


def test_readme_tem_a_regra_das_mudancas_incompativeis():
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert "Mudanças incompatíveis" in readme and "variável de ambiente" in readme


def test_template_de_pr_tem_a_caixa_da_api_publica():
    template = (RAIZ / ".github/pull_request_template.md").read_text(encoding="utf-8")
    assert "- [ ] Esta PR muda a API pública do SDK? Se sim, o CHANGELOG foi atualizado em Mudanças incompatíveis." in template

