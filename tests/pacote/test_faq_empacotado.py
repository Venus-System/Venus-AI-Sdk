"""Os documentos do FAQ vão dentro do pacote: quem instala o SDK via pip
(a API) usa a mesma fonte da verdade, sem copiar a pasta."""

import json
import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
_LER_FAQ_DIR = (
    "import json, pathlib; from venus_sdk.config.settings import FAQ_DIR; "
    "print(json.dumps({'faq_dir': FAQ_DIR, 'md': sorted(p.name for p in pathlib.Path(FAQ_DIR).glob('*.md'))}))"
)


def _faq_dir_num_processo_novo(cwd: Path, pythonpath: str | None = None) -> dict:
    # Processo novo, sem FAQ_DIR no ambiente e rodando de outra pasta: só vale
    # o padrão do SDK, não o `data/faq` de quem roda.
    env = {chave: valor for chave, valor in os.environ.items() if chave != "FAQ_DIR"}
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    saida = subprocess.run([sys.executable, "-c", _LER_FAQ_DIR], cwd=cwd, env=env,
                           capture_output=True, text=True, check=True)
    return json.loads(saida.stdout.strip().splitlines()[-1])


def test_faq_dir_padrao_e_a_pasta_do_pacote(tmp_path):
    resultado = _faq_dir_num_processo_novo(tmp_path)
    assert Path(resultado["faq_dir"]).parts[-3:] == ("venus_sdk", "data", "faq")
    assert "sobre_o_venus.md" in resultado["md"]


def test_faq_vai_no_wheel_e_funciona_instalado_sem_editavel(tmp_path):
    subprocess.run([sys.executable, "-m", "pip", "wheel", str(RAIZ), "--no-deps", "--no-build-isolation",
                    "-q", "-w", str(tmp_path / "dist")], check=True, capture_output=True)
    [wheel] = (tmp_path / "dist").glob("venus_ai_sdk-*.whl")
    destino = tmp_path / "instalado"
    subprocess.run([sys.executable, "-m", "pip", "install", str(wheel), "--no-deps", "-q",
                    "--target", str(destino)], check=True, capture_output=True)

    resultado = _faq_dir_num_processo_novo(tmp_path, pythonpath=str(destino))
    assert Path(resultado["faq_dir"]).is_relative_to(destino)
    esperados = sorted(p.name for p in (RAIZ / "src" / "venus_sdk" / "data" / "faq").glob("*.md"))
    assert esperados and resultado["md"] == esperados
