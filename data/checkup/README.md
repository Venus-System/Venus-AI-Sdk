# Regras do check-up da rotina

Estes arquivos alimentam o grafo do Neo4j (`scripts/sincronizar_neo4j.py`)
e definem os avisos que a Venus dá sobre a rotina do usuário.

## `tipos_de_ativo.csv`
Qual ingrediente (nome INCI, como está em `venus.ingredients.inci_name`) é de
qual tipo de ativo. Os **filtros UV** vêm de dois lugares: da categoria "Filtro UV" que já existe
no banco (`venus.ingredient_categories`, quando presente) e dos filtros mais
comuns listados aqui (reserva para bancos sem essa categoria).

## `regras.csv`
| coluna | valores |
|---|---|
| `tipo`, `outro_tipo` | nomes de tipo (os de `tipos_de_ativo.csv` ou "Filtro UV") |
| `regra` | `conflita_com` · `precisa_de` · `vem_antes_de` |
| `periodo` | só para `precisa_de`: `manha` ou `noite` (vazio = qualquer período) |
| `severidade` | só para `conflita_com`: `leve`, `moderada` ou `alta` |
| `motivo` | frase curta que a Venus usa para explicar o aviso |
| `fonte` | referência que sustenta a regra |
| `revisado` | `sim` quando o time de cosmético revisou e preencheu a fonte |

**A lista atual é um ponto de partida (`revisado = nao`).** Antes de ir para
produção, o time de cosmético deve revisar cada regra e preencher a `fonte`.

Para mudar uma regra: edite o CSV e abra uma PR. A próxima sincronização leva
a mudança para o Neo4j.
