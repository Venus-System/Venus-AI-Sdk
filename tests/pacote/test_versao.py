"""A versão do SDK em tempo de execução é a mesma do `pyproject.toml`.

A API instala o SDK por tag; se o código mudar sem subir a versão, dois
códigos diferentes ficam com o mesmo número e não dá para saber qual está
rodando em produção.
"""

import re
from pathlib import Path

import venus_sdk

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _versao_do_pyproject() -> str:
    return re.search(r'^version = "([^"]+)"', PYPROJECT.read_text(encoding="utf-8"), re.M).group(1)


def test_versao_em_tempo_de_execucao_e_a_do_pyproject():
    # Falha também se o pacote instalado na venv for de outra versão
    # (reinstale com `pip install -e .`).
    assert venus_sdk.__version__ == _versao_do_pyproject()


def test_versao_e_0_2_0_ou_maior():
    maior, menor = (int(parte) for parte in _versao_do_pyproject().split(".")[:2])
    assert (maior, menor) >= (0, 2)
