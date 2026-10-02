# Check-up da rotina no Neo4j

Depois de montar a rotina, a Venus avisa sobre **conflitos** entre ativos do
mesmo período, **ativos que exigem algo que falta** (ex.: retinoide sem
protetor solar de manhã), **ordem** de aplicação a ajustar e **produtos
repetidos**. O check-up é opcional: sem Neo4j, a rotina sai como sempre.

## Fluxo

```
data/checkup/*.csv  ─┐
(tipos e regras)      ├──► scripts/sincronizar_neo4j.py ──► Neo4j ◄── check_routine_health
Postgres            ─┘     (só LÊ o Postgres)              (cópia)     (agente de rotina)
```

1. **Regras** — `data/checkup/tipos_de_ativo.csv` (ingrediente -> tipo de
   ativo) e `data/checkup/regras.csv` (conflita com / precisa de / vem antes
   de). Mudança de regra = PR. Ver `data/checkup/README.md`.
2. **Cópia** — `python scripts/sincronizar_neo4j.py` lê produtos,
   ingredientes, composição e favoritos do Postgres e as regras dos CSVs, e
   refaz o grafo. Roda toda noite e quando os CSVs mudam. Durante a cópia,
   quem consulta vê os dados anteriores (nada é apagado antes de o novo estar
   gravado).
3. **Chat** — a tool `check_routine_health` monta a rotina do dia (manhã e
   noite, como em `suggest_routine`), roda as 4 consultas em paralelo e
   devolve os avisos do período pedido.

## Modelo do grafo

```
(:Usuario {id})-[:FAVORITOU]->(:Produto {id, nome, categoria})
(:Produto)-[:CONTEM {posicao}]->(:Ingrediente {id, inci, nome})
(:Ingrediente)-[:E_DO_TIPO]->(:TipoAtivo {nome})
(:TipoAtivo)-[:CONFLITA_COM {severidade, motivo, fonte, revisado}]->(:TipoAtivo)
(:TipoAtivo)-[:PRECISA_DE {periodo, motivo, fonte, revisado}]->(:TipoAtivo)
(:TipoAtivo)-[:VEM_ANTES_DE {motivo, fonte, revisado}]->(:TipoAtivo)
```

- O **período** (manhã/noite) e a **ordem** de cada produto NÃO ficam no
  grafo: vêm da rotina que a Venus montou (`tools/rotina.py`) e entram como
  parâmetro das consultas.
- Chaves únicas (`Produto.id`, `Ingrediente.id`, `Usuario.id`,
  `TipoAtivo.nome`) são criadas pelo próprio script.

## As consultas (`src/venus_sdk/checkup/consultas.py`)

| Aviso | Regra |
|---|---|
| Conflito | Dois produtos do **mesmo período** com tipos que `CONFLITA_COM` (cada par uma vez). |
| Faltando | Produto com tipo que `PRECISA_DE` outro, sem nenhum produto com esse outro tipo **no período exigido** (o protetor tem que ser de manhã). |
| Ordem | Produto cujo tipo `VEM_ANTES_DE` o de outro, mas que está depois na rotina. |
| Repetido | Dois produtos da mesma categoria, no mesmo período, com ≥ 60% dos ingredientes em comum. |

## Configuração

```
NEO4J_URI=neo4j+s://xxxx.databases.neo4j.io     # ou bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=...
```

- **Local:** `docker run -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/senha-local neo4j:5`
- **QA/apresentação:** Neo4j Aura Free (neo4j.com/cloud/aura).
- **API:** instalar o SDK com o extra `[neo4j]` e passar
  `tools_rotina_extras=montar_tools_checkup(pool)` em `compilar_grafo_venus`.

## Quando o Neo4j falha

`check_routine_health` devolve `{"checado": false}` (sem `NEO4J_URI`, Neo4j
fora do ar, credencial errada) e a Venus não comenta o check-up. O timeout de
conexão é de 5 s.

## Testes

- `tests/checkup/test_checkup.py` — sem Neo4j (executor falso): formato dos
  avisos, filtro por período, plano B, identidade, leitura dos CSVs.
- `tests/checkup/test_checkup_neo4j.py` — marcados `integration`, contra um
  Neo4j de verdade (`NEO4J_URI`): grava um grafo pequeno e confere cada
  consulta, inclusive o caso "retinoide à noite + ácido de manhã não é
  conflito".
