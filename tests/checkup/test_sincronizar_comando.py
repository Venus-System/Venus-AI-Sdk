"""`python -m venus_sdk.checkup.sincronizar` roda a partir do SDK instalado
(a API não tem o checkout do SDK): as regras vão dentro do pacote e o módulo
tem o próprio ponto de entrada."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from venus_sdk.checkup import sincronizar


def test_regras_padrao_vem_do_pacote():
    pasta = Path(sincronizar.PASTA_REGRAS_PADRAO)
    assert pasta.parts[-3:] == ("venus_sdk", "data", "checkup")
    assert (pasta / "tipos_de_ativo.csv").is_file() and (pasta / "regras.csv").is_file()


def test_modulo_sem_configuracao_avisa_e_sai_com_erro(tmp_path):
    env = {chave: valor for chave, valor in os.environ.items()
           if not chave.startswith("NEO4J_") and chave != "DATABASE_URL"}
    saida = subprocess.run([sys.executable, "-m", "venus_sdk.checkup.sincronizar"], cwd=tmp_path, env=env,
                           capture_output=True, text=True, timeout=60)
    assert saida.returncode == 1
    assert "NEO4J_URI" in saida.stdout and "DATABASE_URL" in saida.stdout


def test_main_imprime_o_resumo_da_sincronizacao(monkeypatch, capsys):
    fechados = []

    class _Recurso:
        def __init__(self, nome):
            self.nome = nome

        async def close(self):
            fechados.append(self.nome)

    async def criar_pool(url, **kwargs):
        return _Recurso("pool")

    async def sincronizar_falso(pool, executar, pasta):
        assert pasta == sincronizar.PASTA_REGRAS_PADRAO
        return {"produtos": 12, "regras": 30}

    monkeypatch.setattr(sincronizar, "_configuracao_ok", lambda: True)
    monkeypatch.setattr(sincronizar, "_url_do_postgres", lambda: "postgresql://teste")
    monkeypatch.setattr(sincronizar, "_criar_pool", criar_pool)
    monkeypatch.setattr(sincronizar, "_criar_driver", lambda: _Recurso("driver"))
    monkeypatch.setattr(sincronizar, "sincronizar", sincronizar_falso)

    assert asyncio.run(sincronizar.main(sincronizar.PASTA_REGRAS_PADRAO)) == 0
    saida = capsys.readouterr().out
    assert "produtos: 12" in saida and "regras: 30" in saida
    assert sorted(fechados) == ["driver", "pool"]
