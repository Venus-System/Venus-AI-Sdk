# Changelog

Mudanças por versão do SDK. A API instala o SDK por tag (ver "Versões e
release" no README); cada tag tem a sua seção aqui, dividida em:

- **Mudanças incompatíveis:** o que quem usa o SDK precisa mudar ou conferir
  ao atualizar, com o antes e o depois. Inclui mudança de assinatura
  pública, de variável de ambiente e de comportamento padrão.
- **Novidades:** o que passou a existir, sem quebrar quem já usava.
- **Correções:** bugs corrigidos.

## 0.4.0

Expressão da Venus para a web. Tudo desde `v0.3.0`.

### Mudanças incompatíveis
- Nenhuma mudança incompatível. O campo novo no estado é opcional para quem
  lê, e a `resposta_final` continua sem nenhuma marca.

### Novidades
- **Campo `expressao` no estado** (`EstadoVenus`), do tipo `Expressao`
  (`venus_sdk.state`): `"neutra"` ou `"magoada"`. É a cara da Venus que
  acompanha a resposta, para a web mostrar a Veninha "chorando" quando alguém
  a ofende. Quem escolhe o valor é o código, numa lista fechada:
  - na reação a ofensa, o roteador começa a resposta com a linha
    `REACAO=magoada` (protocolo novo em `prompts/router.py`). O nó tira a
    linha do texto, com qualquer valor, e só `magoada` numa resposta direta
    mantida como veio vira `"magoada"`;
  - todo o resto é `"neutra"`: rota de especialista, confirmação de
    agendamento, valor desconhecido na marca, texto trocado pelas redes de
    segurança do roteador ou pelo guardrail de saída;
  - o guardrail de entrada zera o campo a cada turno, então uma `magoada`
    não volta pelo checkpointer no turno seguinte.

  **Como usar:** `estado.get("expressao", "neutra")` depois do `ainvoke`.

### Correções
- **Reclamação tratada como ofensa.** Com o LLM real, "essa resposta foi
  péssima" e "não gostei da sua resposta" recebiam a reação a xingamento
  ("Desculpa se fiz algo que te incomodou.."). O prompt do roteador agora diz
  que apontar erro ou reclamar da resposta não é ofensa, e ganhou um exemplo
  de reclamação (`ROUTER_SHOT_7C`). Conferido com o LLM real em 18 mensagens:
  ofensas viram `magoada`; reclamação, saudação e elogio ficam `neutra`.
- **`[nome]` na resposta direta do roteador.** O LLM às vezes deixava o
  marcador no lugar do nome ("desculpa mesmo, [nome]!!"), e a resposta direta
  não passa pelo orquestrador, que já barrava isso. Agora o marcador sai do
  texto, com a vírgula de antes; link markdown (`[FAQ](https://...)`) fica.

## 0.3.0

Revisão técnica 3. Tudo desde `v0.2.0` (`4518636`).

### Mudanças incompatíveis
- **Download do modelo do FastEmbed** (índice local do FAQ e
  `get_embed_model`).
  **Antes:** sem o modelo no cache, a biblioteca tentava baixar 3 vezes,
  esperando 3, 9 e 27 s, antes de cair no `EmbeddingsHash`; sem rede, o
  startup levava cerca de 40 s.
  **Depois:** o SDK confere o cache primeiro. Com `HF_HUB_OFFLINE=1` ou
  `VENUS_FASTEMBED_DOWNLOAD=0`, cai no `EmbeddingsHash` na hora. Com download
  permitido, espera no máximo `VENUS_FASTEMBED_TIMEOUT_SEGUNDOS` (padrão 15 s).
  **O que conferir:** se o ambiente baixa o modelo no primeiro uso e a rede é
  lenta, aumente `VENUS_FASTEMBED_TIMEOUT_SEGUNDOS`, ou deixe o modelo no
  cache (a imagem Docker da API já faz isso).
- **LLM do classificador do guardrail.**
  **Antes:** `nodes/guardrails.py` chamava `get_llm_rapido()`, que percorre a
  cadeia rápida inteira, e uma falha passava sem registro.
  **Depois:** o classificador chama `get_llm_guardrail()`, com o 1º elo e um
  elo de outro provedor. Tem disjuntor: depois de
  `VENUS_GUARDRAIL_LLM_FALHAS_PARA_ABRIR` falhas seguidas (padrão 5), fica
  pausado por `VENUS_GUARDRAIL_LLM_PAUSA_SEGUNDOS` (padrão 60). Os contadores
  ficam em `estatisticas_guardrail_llm()`.
  **O que mudar:** em testes, simule o LLM com
  `monkeypatch.setattr(guardrails, "get_llm_guardrail", ...)`, não com
  `get_llm_rapido`. O estado do disjuntor é por processo: zere com
  `guardrails._reiniciar_classificador()` entre testes.
- **Regras do check-up do Neo4j.**
  **Antes:** `data/checkup/` na raiz do repositório e
  `python scripts/sincronizar_neo4j.py`, que só funcionavam a partir do
  checkout.
  **Depois:** as regras vão dentro do pacote (`venus_sdk/data/checkup/`), e o
  comando é `python -m venus_sdk.checkup.sincronizar [--pasta X]`. O script
  antigo continua como atalho.
  **O que mudar:** quem passava `data/checkup` à mão deve usar
  `venus_sdk.checkup.sincronizar.PASTA_REGRAS_PADRAO`.
- **Índice do FAQ ainda em construção.**
  **Antes:** `montar_tools_faq(indice)` só recusava `indice=None`.
  **Depois:** um índice com `pronto=False` também é recusado; o agente FAQ
  responde `erro_tecnico` e tenta de novo na próxima pergunta. Objetos sem o
  atributo `pronto` continuam valendo como prontos.
- **Comentários HTML nos `.md` do FAQ.**
  **Antes:** eram indexados como texto.
  **Depois:** o índice local e a ingestão no Qdrant descartam esses
  comentários.
  **O que fazer:** com `QDRANT_URL`, rode `python -m venus_sdk.rag.faq_ingest`
  depois de atualizar, para a coleção receber os documentos novos.
- **Juiz e tools de cálculo** (`tools/calculos.py`). Nenhuma assinatura
  pública mudou, mas há duas mudanças de comportamento.
  **Antes:** o Juiz só conferia números pelo LLM, e os agentes de Ingrediente
  e Rotina faziam contas de cabeça.
  **Depois:** os dois agentes recebem 2 tools de cálculo cada, e o prompt
  manda nunca calcular de cabeça. O Juiz reprova sem chamar o LLM quando o
  número devolvido por uma tool de cálculo não aparece na resposta.

### Novidades
- `venus_sdk.checkup.sincronizar` com ponto de entrada de linha de comando,
  e as regras do check-up empacotadas no wheel.
- `estatisticas_guardrail_llm()` e `llm.models.get_llm_guardrail()`.
- `trocar_codigo_por_token(..., code_verifier=...)`: troca do `code` de um OAuth
  feito com PKCE no app (parâmetro opcional; sem ele, nada muda).
- `vector_build.modelo_em_cache()`, `pasta_do_cache()` e
  `ModeloIndisponivel`.
- `tools/calculos.py`:
  - `converter_concentracao` e `comparar_concentracao_com_limite`, para o
    agente de Ingrediente;
  - `calcular_tempo_de_uso` e `calcular_datas_de_aplicacao`, para o agente de
    Rotina;
  - funções puras com `resultado`, `valor_para_citar` e `formula`.
- `rag.web.BuscaWebMcp` e `compilar_grafo_venus(busca_web_faq=...)`: a busca
  na web do FAQ passa pelo servidor MCP da Tavily (`tavily_search`), com a
  busca direta de reserva.
- Avaliação reproduzível:
  - `.github/workflows/avaliar-rag.yaml`, que falha se o hit@3 com FastEmbed
    ficar abaixo de 0,8;
  - `tests/manual/avaliar_guardrail.py` e
    `.github/workflows/avaliar-guardrail.yaml`;
  - resultados em `docs/avaliacao-rag.md` e `docs/avaliacao-guardrail.md`.

### Correções
- **FAQ:** seções com título em forma de pergunta. "vocês vendem meus dados?"
  agora traz o `privacidade_e_dados.md` em primeiro lugar. No índice local,
  hit@3 de 0,88 para 0,96 e "não sei" correto de 86% para 100%. A autoria do
  app continua sem resposta (TODO para o time), em vez de inventada.
- **Guardrail:** o fail-open do classificador LLM não é mais silencioso. Gera
  log `warning` com `evento=guardrail_llm_fail_open`, sem o texto do usuário,
  e é contado.

## 0.2.0

Correções das revisões técnicas 1 e 2 e check-up da rotina. Tudo desde
`v0.1.0` (`e91d4f1`).

### Mudanças incompatíveis
- **Classificador LLM do guardrail.**
  **Antes:** desligado, salvo `VENUS_GUARDRAIL_LLM=1`.
  **Depois:** ligado por padrão, com uma chamada extra de LLM rápido por
  mensagem; `VENUS_GUARDRAIL_LLM=0` desliga.
  **O que mudar:** nos testes que usam LLM roteirizado, defina
  `VENUS_GUARDRAIL_LLM=0`, senão o classificador consome uma resposta do
  roteiro.
- **`thread_id` do A2A.**
  **Antes:** o `context_id` era usado direto.
  **Depois:** passa a ser `a2a:<context_id>`, e um `context_id` inválido gera
  `InvalidParamsError`.
  **O que conferir:** conversas A2A gravadas antes não são retomadas, e quem
  lia o checkpointer pelo `context_id` precisa acrescentar o prefixo.
- **Pasta padrão do FAQ.**
  **Antes:** `FAQ_DIR` apontava para `<checkout>/data/faq` ou para
  `./data/faq`.
  **Depois:** aponta para os documentos empacotados em
  `venus_sdk/data/faq`.
  **O que mudar:** apague cópias locais da pasta. Para usar outra pasta,
  defina `FAQ_DIR`.
- **`venus_sdk` virou pacote normal.**
  **Antes:** era um pacote de namespace, sem `__init__.py`.
  **Depois:** tem `venus_sdk/__init__.py`, com `__version__`.
- **Trechos do índice local do FAQ.**
  **Antes:** pedaços de 700 caracteres.
  **Depois:** um ou mais pedaços por seção do markdown, cada um começando com
  o caminho de títulos (`Documento > Seção`) e com `metadata["secao"]`.
  **O que conferir:** quem comparava o `trecho` com o texto bruto dos `.md`
  deve ignorar a primeira linha.
- **Criação dos agentes.**
  **Antes:** `create_react_agent` do LangGraph.
  **Depois:** `create_agent` do LangChain, o que exige `langchain>=1.0`. Em
  `montar_agente_mcp(..., prompt=...)`, `prompt` agora aceita também uma
  função, que é avaliada a cada execução.

### Novidades
- **Check-up da rotina com Neo4j:** regras de ativos em CSV, sincronização e
  tool de check-up para o agente de rotina (`44b724e`).
- **Rotina:** o horário de cada produto passa a ser decidido também pela
  fórmula (`58264f9`).
- `venus_sdk.__version__`, lida dos metadados do pacote.
- **FAQ empacotado no wheel** (`package-data`).
- **Avaliação manual do RAG** (`tests/manual/avaliar_rag.py`, com
  `--faq-dir` e `--embeddings fastembed|hash`) e `docs/avaliacao-rag.md`.
- Processo de release por tag e link para os diagramas de arquitetura.

### Correções
- **FAQ:** falha ao montar o agente responde `erro_tecnico` em vez de
  derrubar o grafo. O índice local usa FastEmbed e só cai no
  `EmbeddingsHash`, com aviso, quando ele não está disponível (`12a2c91`).
  A divisão por seção levou o hit@3 do índice local de 0,79 para 0,88.
- **Schema:** coluna `firebase_uid TEXT UNIQUE` em `venus.users`
  (`5e7df9e`).
- **Guardrail:**
  - injeção detectada por verbo de comando + alvo sensível, em português e
    inglês;
  - "DAN" só bloqueado em contexto de modo ou persona;
  - letras espaçadas e leetspeak normalizados.
- **Check-up:** gravações no Neo4j sem produto cartesiano (`81e234c`).

## 0.1.0

Primeira versão com tag (`e91d4f1`): multiagente, memória de longo prazo,
RAG do FAQ (local e Qdrant), MCP, A2A, juiz, guardrails e agendamento da
rotina no Google Calendar.

### Mudanças incompatíveis
- Nenhuma mudança incompatível (primeira versão com tag).

### Novidades
- Versão inicial.

### Correções
- Nenhuma.
