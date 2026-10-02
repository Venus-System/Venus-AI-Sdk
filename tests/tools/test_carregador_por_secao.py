"""O índice local divide os .md por seção do markdown: um trecho nunca junta
duas seções, e cada um carrega o caminho de títulos de onde veio. Sem isso,
o `api_de_classificacao.md` (739 linhas) virava pedaços de 700 caracteres
sem contexto que apareciam em perguntas de outros assuntos."""

from venus_sdk.rag import carregar_documentos

DOCUMENTO = """# API de Classificação

Introdução curta.

## Privacidade

Os dados não são vendidos.

## Nota

### Fórmula

A nota vai de 0 a 100.

```python
# comentário dentro de código, não é título
nota = 0
```
"""


def _trechos(tmp_path, texto=DOCUMENTO, **kwargs):
    (tmp_path / "api.md").write_text(texto, encoding="utf-8")
    return carregar_documentos(tmp_path, **kwargs)


def test_cada_trecho_fica_numa_secao_so(tmp_path):
    conteudos = [doc.page_content for doc in _trechos(tmp_path)]
    assert len(conteudos) == 3
    assert "vendidos" in conteudos[1] and "0 a 100" not in conteudos[1]
    assert "0 a 100" in conteudos[2] and "comentário dentro de código" in conteudos[2]


def test_trecho_comeca_com_o_caminho_de_titulos(tmp_path):
    trechos = _trechos(tmp_path)
    assert trechos[0].page_content.startswith("API de Classificação\n\n")
    assert trechos[1].page_content.startswith("API de Classificação > Privacidade\n\n")
    assert trechos[2].page_content.startswith("API de Classificação > Nota > Fórmula\n\n")
    assert trechos[2].metadata == {"fonte": "api.md", "trecho": 3, "secao": "API de Classificação > Nota > Fórmula"}


def test_secao_longa_e_subdividida_com_o_titulo_em_cada_pedaco(tmp_path):
    longa = "# Doc\n\n## Longa\n\n" + "\n\n".join(f"Parágrafo {i} " + "palavra " * 30 for i in range(10))
    trechos = _trechos(tmp_path, longa, tamanho_chunk=400, sobreposicao=0)
    assert len(trechos) > 1
    assert all(t.page_content.startswith("Doc > Longa\n\n") for t in trechos)


def test_txt_continua_sem_secao(tmp_path):
    (tmp_path / "nota.txt").write_text("# não é markdown\ntexto", encoding="utf-8")
    [trecho] = carregar_documentos(tmp_path)
    assert trecho.page_content.startswith("# não é markdown") and "secao" not in trecho.metadata
