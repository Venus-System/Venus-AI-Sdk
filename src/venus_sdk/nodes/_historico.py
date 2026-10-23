"""Trecho recente da conversa para os nós que não recebem o histórico inteiro
(especialistas e Agente Juiz).

Antes só o roteador via o histórico: "sim, sugira", "qual vc recomendaria?" ou
"vc acabou de contraindicar esse produto" chegavam ao especialista sem o turno
anterior, e ele reanalisava o produto do zero a cada mensagem (conversa real de
2026-10-20). O trecho é só CONTEXTO para resolver referências — dado de
produto/ingrediente continua vindo exclusivamente das tools."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from venus_sdk.nodes.state import EstadoVenus

# Últimas mensagens (usuário + Venus) incluídas, sem contar a deste turno.
MAX_MENSAGENS_HISTORICO = 6
_MAX_CARACTERES_POR_MENSAGEM = 500


def _texto(mensagem: object) -> str:
    conteudo = getattr(mensagem, "content", "")
    texto = conteudo if isinstance(conteudo, str) else str(conteudo)
    texto = " ".join(texto.split())
    if len(texto) > _MAX_CARACTERES_POR_MENSAGEM:
        return texto[:_MAX_CARACTERES_POR_MENSAGEM] + "…"
    return texto


def historico_recente(estado: EstadoVenus) -> str | None:
    """"Usuário: ...\\nVenus: ..." com as últimas trocas ANTERIORES a este turno,
    ou `None` se a conversa acabou de começar.

    `historico` já termina com a mensagem deste turno (gravada por
    `no_guardrail_entrada`), que é descartada aqui — ela chega ao especialista
    como PERGUNTA_ORIGINAL."""
    mensagens = list(estado.get("historico") or [])
    if mensagens and isinstance(mensagens[-1], HumanMessage):
        mensagens = mensagens[:-1]
    linhas = []
    for mensagem in mensagens[-MAX_MENSAGENS_HISTORICO:]:
        if isinstance(mensagem, HumanMessage):
            autor = "Usuário"
        elif isinstance(mensagem, AIMessage):
            autor = "Venus"
        else:
            continue
        texto = _texto(mensagem)
        if texto:
            linhas.append(f"{autor}: {texto}")
    return "\n".join(linhas) or None
