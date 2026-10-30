"""Nós de memória de longo prazo — perfil do usuário persistido entre
conversas (`thread_id`) diferentes, via `store` (ver `memory/store.py`).

Distinto do checkpointer (`memory/checkpointer.py`): o checkpointer guarda o
histórico bruto de UMA conversa (`thread_id`); estes nós guardam um resumo de
fatos duráveis sobre o usuário (`usuario_id`), consultável em qualquer
conversa futura dele.

`no_carregar_memoria` roda logo após o guardrail de entrada (antes do
roteador) e `no_atualizar_memoria` roda por último, depois do guardrail de
saída — ver `flows/venus_flow.py`.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Any

from langgraph.store.base import BaseStore

from venus_sdk.prompts.guardrail_rules import contem_tentativa_de_injecao
from venus_sdk.llm.models import get_llm_rapido
from venus_sdk.nodes._json import extrair_objeto_json
from venus_sdk.prompts.memoria import MEMORIA_PROMPT_COMPLETO
from venus_sdk.nodes.state import EstadoVenus

logger = logging.getLogger(__name__)

NAMESPACE_MEMORIAS = "memorias"
CHAVE_PERFIL = "perfil"
# Data da última atualização de cada campo do perfil (`{"tipo_pele": "2026-10-30"}`),
# numa chave à parte para o perfil continuar sendo só os fatos.
CHAVE_DATAS = "perfil_datas"
# Campo especial da saída do extrator: nomes de campos a apagar do perfil.
CAMPO_REMOVER = "remover"

# Alergia não é memória: a fonte de verdade é o cadastro do app
# (`get_user_allergies`), que o usuário corrige por lá. Na memória, "sou
# alérgica a X" só se acumulava — "não sou mais" nunca saía da lista — e
# competia com o cadastro. Perfis antigos que já têm o campo o perdem ao carregar.
_CAMPOS_FORA_DA_MEMORIA = frozenset({"alergias", "alergia", "sensibilidades"})
_TTL_PADRAO_DIAS = 180


def _namespace(usuario_id: str) -> tuple[str, str]:
    return (NAMESPACE_MEMORIAS, usuario_id)


def _ttl_dias() -> int:
    """`VENUS_MEMORIA_TTL_DIAS` (padrão 180): fato não reafirmado há mais tempo
    que isso deixa de valer. 0 desliga."""
    try:
        return max(0, int(os.getenv("VENUS_MEMORIA_TTL_DIAS", str(_TTL_PADRAO_DIAS))))
    except ValueError:
        return _TTL_PADRAO_DIAS


def _perfil_vigente(perfil: dict[str, Any], datas: dict[str, str], hoje: date) -> dict[str, Any]:
    """`perfil` sem os campos que não são memória e sem os expirados. Campo sem
    data (gravado antes de existir `CHAVE_DATAS`) não expira."""
    ttl = _ttl_dias()
    vigente = {}
    for campo, valor in perfil.items():
        if campo in _CAMPOS_FORA_DA_MEMORIA:
            continue
        try:
            idade = (hoje - date.fromisoformat(datas[campo])).days if campo in datas else 0
        except (TypeError, ValueError):
            idade = 0
        if ttl and idade > ttl:
            continue
        vigente[campo] = valor
    return vigente


def _ler_perfil(store: BaseStore, usuario_id: str, hoje: date) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Perfil vigente salvo (`None` se o usuário ainda não tem perfil) e as datas."""
    item = store.get(_namespace(usuario_id), CHAVE_PERFIL)
    if item is None or not isinstance(item.value, dict):
        return None, {}
    datas_item = store.get(_namespace(usuario_id), CHAVE_DATAS)
    datas = datas_item.value if datas_item and isinstance(datas_item.value, dict) else {}
    return _perfil_vigente(item.value, datas, hoje), datas


def apagar_memoria(store: BaseStore, usuario_id: str) -> None:
    """Apaga toda a memória de longo prazo do usuário (perfil e datas). Para o
    backend chamar quando a pessoa exclui a conta ou pede a exclusão dos dados
    (LGPD). O histórico das conversas fica no checkpointer, por `thread_id`, e
    é apagado à parte."""
    store.delete(_namespace(usuario_id), CHAVE_PERFIL)
    store.delete(_namespace(usuario_id), CHAVE_DATAS)
    logger.info("Memória de longo prazo apagada (usuario_id=%s)", usuario_id)


def no_carregar_memoria(estado: EstadoVenus, *, store: BaseStore) -> EstadoVenus:
    """Busca o perfil de longo prazo do usuário (se houver) e grava em
    `memorias_usuario`, pra ser injetado no roteador/especialistas (ver
    `MEMORIA_USUARIO=` em `nodes/roteador.py`/`nodes/especialistas.py`).

    Sem `store` configurado (`compilar_grafo_venus(store=...)` não recebeu
    um) ou sem `usuario_id` no estado, não faz nada — a conversa segue sem
    memória de longo prazo, como antes de esses campos existirem.
    """
    usuario_id = estado.get("usuario_id")
    if store is None or not usuario_id:
        return {}

    try:
        perfil, _ = _ler_perfil(store, usuario_id, date.today())
    except Exception:
        # Store fora do ar/lento: o turno segue sem memória de longo prazo em
        # vez de a requisição inteira virar erro.
        logger.exception("Falha ao ler memória de longo prazo (usuario_id=%s)", usuario_id)
        return {}
    return {"memorias_usuario": perfil or None}


def no_atualizar_memoria(estado: EstadoVenus, *, store: BaseStore) -> EstadoVenus:
    """Ao final do turno, decide (via LLM) se algo dito nesta troca merece
    virar memória de longo prazo e, se sim, funde no perfil existente.

    Roda depois do guardrail de saída — não altera `resposta_final`, só
    atualiza o `store`. Qualquer falha aqui (LLM, parsing) é engolida: isso
    nunca deve derrubar uma resposta que já foi validada e está indo pro
    usuário.
    """
    usuario_id = estado.get("usuario_id")
    if store is None or not usuario_id or estado.get("entrada_bloqueada"):
        # Turno bloqueado: a mensagem não é confiável e a memória nem foi
        # carregada — mesclar agora apagaria o perfil salvo.
        return {}

    # `mensagem_anonimizada` (a entrada de fato, já sem dado sensível) — não
    # `pergunta_original`, que só existe quando houve rota de especialista.
    pergunta = estado.get("mensagem_anonimizada") or estado.get("mensagem_usuario", "")
    resposta = estado.get("resposta_final") or ""
    perfil_atual = estado.get("memorias_usuario") or {}

    entrada = (
        f"PERFIL_ATUAL={json.dumps(perfil_atual, ensure_ascii=False)}\n"
        f"PERGUNTA_USUARIO={pergunta}\n"
        f"RESPOSTA_VENUS={resposta}"
    )
    mensagens = [("system", MEMORIA_PROMPT_COMPLETO), ("human", entrada)]

    try:
        texto = (get_llm_rapido().invoke(mensagens).content or "").strip()
    except Exception:
        logger.exception("Falha ao chamar o LLM extrator de memória (usuario_id=%s)", usuario_id)
        return {}

    fatos_novos = _extrair_fatos_novos(texto)
    if not fatos_novos:
        return {}

    hoje = date.today()
    try:
        # Relê o perfil: outra conversa do mesmo usuário pode ter gravado
        # fatos depois que este turno carregou a memória.
        perfil_salvo, datas = _ler_perfil(store, usuario_id, hoje)
        perfil_atualizado = _mesclar_perfil(perfil_atual if perfil_salvo is None else perfil_salvo, fatos_novos)
        datas = {campo: data for campo, data in datas.items() if campo in perfil_atualizado}
        datas.update({campo: hoje.isoformat() for campo in fatos_novos if campo in perfil_atualizado})
        store.put(_namespace(usuario_id), CHAVE_PERFIL, perfil_atualizado)
        store.put(_namespace(usuario_id), CHAVE_DATAS, datas)
        # Auditoria: quais campos mudaram, nunca os valores.
        logger.info(
            "Memória atualizada (usuario_id=%s): gravados=%s removidos=%s", usuario_id,
            sorted(c for c in fatos_novos if c != CAMPO_REMOVER), fatos_novos.get(CAMPO_REMOVER) or [],
        )
    except Exception:
        # A resposta já foi validada e está indo pro usuário: falha ao gravar
        # a memória só perde a memória, nunca a resposta.
        logger.exception("Falha ao gravar memória de longo prazo (usuario_id=%s)", usuario_id)
        return {}
    return {"memorias_usuario": perfil_atualizado}


def _extrair_fatos_novos(texto: str) -> dict[str, Any] | None:
    if not texto or texto.strip().upper() == "NADA":
        return None
    try:
        fatos = extrair_objeto_json(texto)
    except ValueError:
        logger.warning("Extrator de memória não devolveu JSON válido (%d caracteres)", len(texto))
        return None
    if contem_tentativa_de_injecao(json.dumps(fatos, ensure_ascii=False)):
        # A memória volta ao prompt em todo turno: um "fato" com instrução
        # viraria uma injeção permanente.
        logger.warning("Fatos de memória descartados: parecem instrução ao sistema")
        return None
    fatos = {campo: valor for campo, valor in fatos.items() if campo not in _CAMPOS_FORA_DA_MEMORIA}
    remover = fatos.get(CAMPO_REMOVER)
    if CAMPO_REMOVER in fatos:
        remover = [remover] if isinstance(remover, str) else remover
        remover = [str(campo) for campo in remover if campo] if isinstance(remover, list) else []
        if remover:
            fatos[CAMPO_REMOVER] = remover
        else:
            del fatos[CAMPO_REMOVER]
    return fatos or None


def _mesclar_perfil(atual: dict[str, Any], novos: dict[str, Any]) -> dict[str, Any]:
    """Funde `novos` (fatos desta troca) em `atual` (perfil salvo).

    Quando o mesmo campo já existe nos dois lados como LISTA (ex.:
    "alergias" — ver `prompts/memoria.py`), concatena e deduplica em vez de
    sobrescrever: um `{**atual, **novos}` simples perdia a alergia
    mencionada num turno anterior sempre que uma alergia NOVA era declarada
    num turno seguinte. Campo escalar (ex.: "tipo_pele") continua
    sobrescrevendo — ali a atualização/correção é o comportamento certo.

    `novos["remover"]` (lista de campos) apaga esses campos: é como o usuário
    corrige um fato que deixou de valer ("não quero mais tratar acne")."""
    mesclado = {campo: valor for campo, valor in atual.items() if campo not in novos.get(CAMPO_REMOVER, [])}
    for chave, valor_novo in novos.items():
        if chave == CAMPO_REMOVER:
            continue
        valor_atual = mesclado.get(chave)
        if isinstance(valor_atual, list) and isinstance(valor_novo, list):
            mesclado[chave] = valor_atual + [v for v in valor_novo if v not in valor_atual]
        else:
            mesclado[chave] = valor_novo
    return mesclado
