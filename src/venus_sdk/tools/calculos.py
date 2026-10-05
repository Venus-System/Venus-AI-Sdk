"""Tools de cálculo determinísticas — a aritmética sai do LLM.

Conta feita de cabeça pelo modelo é fonte clássica de alucinação, e o Juiz só
pega o erro se o número errado não bater com o retorno de alguma tool. Aqui
cada conta que aparece no fluxo do Venus vira uma função pura (sem LLM, sem
rede, sem banco) com parâmetros tipados — nunca uma calculadora de expressão
livre — e cada resultado traz o número (`resultado`), o texto para citar
(`valor_para_citar`) e a `formula`, para o Juiz conferir a resposta
(`nodes/juiz.py`).

Casos cobertos (levantamento da revisão técnica 3):
- concentração de um ingrediente x limite regulatório
  (`get_ingredient_regulations` traz `max_concentration_value` + `unit`) e
  conversão entre `%`, `mg/g` e `ppm`;
- há quanto tempo o usuário usa um produto (`prompts/comum.py`,
  CONTEXTO_TEMPORAL) e as datas de um uso em dias alternados (o Rotina sugere
  "alternar noites" quando dois ativos conflitam).
Validade depois de aberto (PAO) ficou de fora: o banco não tem esse dado.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from langchain_core.tools import BaseTool, tool

# Fator de cada unidade para ppm (partes por milhão, massa/massa).
# 1 % = 1 g/100 g = 10 mg/g = 10.000 ppm.
_PARA_PPM = {"%": 10_000.0, "mg/g": 1_000.0, "ppm": 1.0}
_APELIDOS = {"g/100g": "%", "% p/p": "%", "%p/p": "%", "mg/kg": "ppm"}
_PPM_MAXIMO = 1_000_000.0  # 100 %
_INTERVALO_MAXIMO_DIAS = 14
_APLICACOES_MAXIMAS = 60
_CASAS_DECIMAIS = 6
_FUSO_BRASILIA = timezone(timedelta(hours=-3))

# Tools cujo `resultado` é um número que a resposta precisa citar (o Juiz
# reprova sem LLM quando o número calculado não aparece).
TOOLS_COM_NUMERO_A_CITAR = frozenset({"converter_concentracao", "comparar_concentracao_com_limite",
                                      "calcular_tempo_de_uso"})


def _unidade(nome: str) -> str:
    chave = (nome or "").strip().lower().replace(" ", "")
    chave = _APELIDOS.get(chave, chave)
    if chave not in _PARA_PPM:
        raise ValueError(f"unidade de concentração desconhecida: {nome!r} (use %, mg/g ou ppm)")
    return chave


def _valor(numero: Any, nome: str) -> float:
    if isinstance(numero, bool) or not isinstance(numero, (int, float)) or not math.isfinite(numero):
        raise ValueError(f"{nome} precisa ser um número finito")
    if numero < 0:
        raise ValueError(f"{nome} não pode ser negativo")
    return float(numero)


def _arredondado(numero: float) -> float:
    return round(numero, _CASAS_DECIMAIS)


def formatar_numero(numero: float) -> str:
    """Número em português, sem zeros sobrando: 20.0 -> "20", 0.5 -> "0,5"."""
    texto = format(Decimal(str(_arredondado(numero))).normalize(), "f")
    return texto.replace(".", ",")


def _em_ppm(valor: float, unidade: str) -> float:
    ppm = valor * _PARA_PPM[unidade]
    if ppm > _PPM_MAXIMO:
        raise ValueError("concentração acima de 100%")
    return ppm


def converter_concentracao(valor: float, de: str, para: str) -> dict[str, Any]:
    """Converte uma concentração entre `%`, `mg/g` e `ppm`."""
    valor = _valor(valor, "valor")
    origem, destino = _unidade(de), _unidade(para)
    resultado = _arredondado(_em_ppm(valor, origem) / _PARA_PPM[destino])
    fator = _PARA_PPM[origem] / _PARA_PPM[destino]
    return {
        "resultado": resultado,
        "unidade": destino,
        "valor_para_citar": f"{formatar_numero(resultado)} {destino}",
        "formula": f"{formatar_numero(valor)} {origem} × {formatar_numero(fator)} = {formatar_numero(resultado)} {destino}",
    }


def comparar_com_limite(valor: float, unidade: str, limite: float, unidade_limite: str) -> dict[str, Any]:
    """Converte `valor` para a unidade do limite e diz se está dentro dele
    (igual ao limite conta como dentro)."""
    limite = _valor(limite, "limite")
    convertido = converter_concentracao(valor, unidade, unidade_limite)
    destino = convertido["unidade"]
    _em_ppm(limite, destino)  # valida o limite também
    dentro = convertido["resultado"] <= _arredondado(limite)
    return {
        "resultado": convertido["resultado"],
        "unidade": destino,
        "limite": _arredondado(limite),
        "dentro_do_limite": dentro,
        "diferenca_para_o_limite": _arredondado(limite - convertido["resultado"]),
        "valor_para_citar": convertido["valor_para_citar"],
        "formula": (f"{convertido['formula']}; {formatar_numero(convertido['resultado'])} "
                    f"{'≤' if dentro else '>'} {formatar_numero(limite)} {destino}"),
    }


def tempo_entre_datas(inicio: date, fim: date) -> dict[str, Any]:
    """Dias corridos entre duas datas, também em semanas completas + dias."""
    if inicio > fim:
        raise ValueError("a data de início é depois da data final")
    dias = (fim - inicio).days
    semanas, resto = divmod(dias, 7)
    return {
        "resultado": dias,
        "semanas_completas": semanas,
        "dias_alem_das_semanas": resto,
        "valor_para_citar": f"{dias} dias",
        "formula": f"{fim.isoformat()} − {inicio.isoformat()} = {dias} dias = {semanas} semana(s) + {resto} dia(s)",
    }


def datas_de_aplicacao(inicio: date, *, intervalo_dias: int, quantidade: int) -> dict[str, Any]:
    """Datas de um uso espaçado (ex.: dias alternados = intervalo 2)."""
    if isinstance(intervalo_dias, bool) or not isinstance(intervalo_dias, int) \
            or not 1 <= intervalo_dias <= _INTERVALO_MAXIMO_DIAS:
        raise ValueError(f"intervalo_dias precisa ser um inteiro de 1 a {_INTERVALO_MAXIMO_DIAS}")
    if isinstance(quantidade, bool) or not isinstance(quantidade, int) or not 1 <= quantidade <= _APLICACOES_MAXIMAS:
        raise ValueError(f"quantidade precisa ser um inteiro de 1 a {_APLICACOES_MAXIMAS}")
    datas = [(inicio + timedelta(days=intervalo_dias * i)).isoformat() for i in range(quantidade)]
    return {
        "resultado": quantidade,
        "datas": datas,
        "formula": f"{inicio.isoformat()} + {intervalo_dias} dia(s) × n, n = 0..{quantidade - 1}",
    }


def _hoje() -> date:
    return datetime.now(_FUSO_BRASILIA).date()


def _data(texto: str, nome: str) -> date:
    try:
        return date.fromisoformat((texto or "").strip())
    except ValueError as erro:
        raise ValueError(f"{nome} precisa estar no formato AAAA-MM-DD") from erro


def _seguro(funcao, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Entrada inválida vira `{"erro": ...}` para o agente corrigir — nunca
    uma exceção que derrube o agente."""
    try:
        return funcao(*args, **kwargs)
    except ValueError as erro:
        return {"erro": str(erro)}


def montar_tools_calculo_ingrediente() -> list[BaseTool]:
    """Concentração e unidade — para o agente de Ingrediente."""

    @tool("converter_concentracao")
    def converter_concentracao_tool(valor: float, de: str, para: str) -> dict:
        """Converte uma concentração entre as unidades `%`, `mg/g` e `ppm`
        (1 % = 10 mg/g = 10.000 ppm). Devolve `resultado`, `unidade`,
        `valor_para_citar` (cite exatamente este texto) e `formula`."""
        return _seguro(converter_concentracao, valor, de, para)

    @tool
    def comparar_concentracao_com_limite(valor: float, unidade: str, limite: float, unidade_limite: str) -> dict:
        """Compara a concentração de um ingrediente com o limite máximo
        (ex.: `max_concentration_value`/`unit` de get_ingredient_regulations),
        convertendo as unidades. Devolve `dentro_do_limite`, o valor convertido
        (`resultado`, `valor_para_citar`), o `limite` e a `formula`."""
        return _seguro(comparar_com_limite, valor, unidade, limite, unidade_limite)

    return [converter_concentracao_tool, comparar_concentracao_com_limite]


def montar_tools_calculo_rotina() -> list[BaseTool]:
    """Datas — para o agente de Rotina."""

    @tool
    def calcular_tempo_de_uso(data_inicio: str, data_fim: str | None = None) -> dict:
        """Há quanto tempo (dias corridos, semanas completas + dias) entre
        `data_inicio` e `data_fim` (padrão: hoje, horário de Brasília). Datas
        no formato AAAA-MM-DD. Cite `valor_para_citar`."""
        try:
            inicio = _data(data_inicio, "data_inicio")
            fim = _data(data_fim, "data_fim") if data_fim else _hoje()
        except ValueError as erro:
            return {"erro": str(erro)}
        return _seguro(tempo_entre_datas, inicio, fim)

    @tool
    def calcular_datas_de_aplicacao(data_inicio: str, intervalo_dias: int, quantidade: int) -> dict:
        """Datas de aplicação de um ativo usado espaçado — ex.: dias
        alternados (`intervalo_dias=2`) — a partir de `data_inicio`
        (AAAA-MM-DD). Devolve a lista `datas` e a `formula`."""
        try:
            inicio = _data(data_inicio, "data_inicio")
        except ValueError as erro:
            return {"erro": str(erro)}
        return _seguro(datas_de_aplicacao, inicio, intervalo_dias=intervalo_dias, quantidade=quantidade)

    return [calcular_tempo_de_uso, calcular_datas_de_aplicacao]
