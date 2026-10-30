# Avaliação do roteador

Resultado de `tests/manual/avaliar_roteamento.py` sobre
`tests/fixtures/avaliacao_roteamento.jsonl`: 45 mensagens em 7 categorias, cada uma
com as rotas aceitas.

**O que é medido:** a taxa de acerto da rota em duas versões.
- **Palavras-chave:** só `rota_por_palavras`, sem LLM. É a linha de base.
- **Roteador:** o nó `no_roteador` completo, com o LLM e as redes de segurança.

**Categorias:**
- `produto`, `ingrediente`, `rotina`, `faq`: uma rota certa.
- `direto`: saudação, agradecimento ou fora de escopo, respondidos pelo próprio roteador.
- `mista`: a pergunta precisa de dois especialistas. Qualquer um dos dois conta como
  acerto, mas o grafo chama só um: a outra metade fica sem resposta. Medir o acerto
  aqui não mede essa lacuna.
- `alergia_no_chat`: "sou alérgica a X". Vale ir a um especialista, ou responder direto
  com a resposta fixa que manda atualizar o cadastro.

O conjunto é conferido no `pytest` (`tests/nodes/test_avaliacao_roteamento.py`), que
também roda o script só com as palavras-chave. A parte com LLM gasta cota e fica de fora.

## Como rodar

```bash
python tests/manual/avaliar_roteamento.py                       # precisa de MISTRAL/GROQ/GEMINI_API_KEY
python tests/manual/avaliar_roteamento.py --sem-llm              # só palavras-chave
python tests/manual/avaliar_roteamento.py --saida-markdown r.md  # também grava a tabela
```

## Resultado: 2026-10-30

**Ambiente:**
- código: branch `fix/rotina-sugestao-alergia`, sobre `ac1ff48`;
- cadeia do roteador (`get_llm_rapido`): `mistral:ministral-14b-latest` →
  `mistral:ministral-8b-latest` → `groq:openai/gpt-oss-20b` → `groq:openai/gpt-oss-120b`
  → `gemini:gemini-3.6-flash`;
- 3 rodadas seguidas; o roteador não é determinístico, então a tabela mostra a faixa.

| Categoria | Palavras-chave | Roteador (3 rodadas) |
|---|---|---|
| produto | 62% (5/8) | 100% (8/8) nas 3 |
| ingrediente | 86% (6/7) | 86% a 100% (6 a 7/7) |
| rotina | 100% (8/8) | 100% (8/8) nas 3 |
| faq | 14% (1/7) | **29% a 57% (2 a 4/7)** |
| direto | 100% (6/6) | 100% (6/6) nas 3 |
| mista | 80% (4/5) | 100% (5/5) nas 3 |
| alergia_no_chat | 100% (4/4) | 100% (4/4) nas 3 |
| **total** | **76% (34/45)** | **89% a 93% (40 a 42/45)** |

## Leitura

- **FAQ é o ponto fraco.** Perguntas sobre o próprio Venus ("como eu apago minha
  conta?", "de onde vêm os dados dos ingredientes?", "o venus substitui um
  dermatologista?") muitas vezes recebem resposta direta do roteador, sem passar pelo
  RAG. Essa resposta pode inventar: numa rodada, "de onde vêm os dados" citou a FDA,
  que não é fonte do catálogo (ANVISA, CosIng e PubChem). Errou em todas as rodadas:
  "de onde vêm os dados dos ingredientes?" e "o venus substitui um dermatologista?".
- **"Como a nota dos produtos é calculada?"** foi para `produto` numa rodada. A
  pergunta é sobre a metodologia (FAQ), não sobre a nota de um produto.
- **"Retinol pode ser usado na gravidez?"** foi para `faq` numa rodada. É ambígua: o
  FAQ fala de limites da IA, e o especialista de ingrediente tem a regulação.
- **Perguntas mistas** vão sempre para um dos dois especialistas certos, mas a outra
  metade da pergunta não é respondida. Isso só se resolve com handoff ou multi-rota.
- O LLM acrescenta 13 a 17 pontos sobre as palavras-chave, quase todos em produto
  e FAQ.
