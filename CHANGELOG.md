# Changelog

Mudanças por versão do SDK. A API instala o SDK por tag (ver "Versões e
release" no README); cada tag tem a sua seção aqui, dividida em:

- **Mudanças incompatíveis:** o que quem usa o SDK precisa mudar ou conferir
  ao atualizar, com o antes e o depois. Inclui mudança de assinatura
  pública, de variável de ambiente e de comportamento padrão.
- **Novidades:** o que passou a existir, sem quebrar quem já usava.
- **Correções:** bugs corrigidos.

## Não lançado

### Mudanças incompatíveis
- **`get_personalized_score` removida.** A tool não existe mais, nem no agente
  de Produto nem no servidor MCP com `--dados-do-usuario` (agora 5 tools com
  `user_id`). A Venus cita só a nota geral; quem chamava a tool por MCP precisa
  ler `venus.personalized_scores` por conta própria. A tabela continua no schema.
- **Texto final sem LLM em produto, ingrediente, rotina e FAQ.** Antes, o
  especialista redigia, o Juiz conferia e o Orquestrador reescrevia. Agora, o
  código monta a resposta só com tools e documentos, sem o tom da Venus. Para
  voltar, defina `VENUS_RESPOSTA_SO_DAS_TOOLS=` (vazia) ou liste só as rotas
  desejadas.
- **Documento técnico fora do FAQ.** `api_de_classificacao.md` foi para `docs/`,
  e a divisão da nota foi para `como_funciona_o_score.md`, sem nomes de campos.
  Com Qdrant, rode `python -m venus_sdk.rag.faq_ingest` para atualizar o índice.
- **FAQ padrão sem web.** Pergunta que os documentos não respondem recebe "Não
  encontrei isso na documentação do Venus.". `busca_web_faq` e `buscar_na_web`
  só valem com `faq` fora de `VENUS_RESPOSTA_SO_DAS_TOOLS`.

### Novidades
- Nó `carregar_nome`: a Venus chama a pessoa pelo primeiro nome do cadastro.
- `nodes/fluxo_fixo.py`: consulta as tools sem ReAct quando o roteiro é sempre o
  mesmo, com o agente de reserva.
- `VENUS_CACHE_DE_PROMPT` (padrão ligado) e data no fim do prompt, para o Mistral
  reaproveitar o prefixo.
- `VENUS_JUIZ_LLM_ROTAS`: rotas em que o Juiz usa LLM.
- Orientação fixa de procurar um profissional em pergunta sobre gravidez,
  amamentação ou reação na pele.
- `tests/manual/avaliar_fontes.py`: mede nota, fragrância e afirmações sem fonte.

## 0.5.0

Alergias conferidas pelo código, perfil completo, governança da memória e
resultado do turno para o trace. Tudo desde `v0.4.0`.

### Mudanças incompatíveis
- **Memória de longo prazo com validade e sem alergia.** Antes, todo fato
  ficava para sempre e a alergia dita no chat era guardada na memória. Agora:
  - fato não reafirmado há mais de `VENUS_MEMORIA_TTL_DIAS` dias (variável
    nova, padrão `180`; `0` desliga) deixa de valer. A data de cada campo fica
    em `perfil_datas`; campo gravado antes da 0.5.0 não tem data e não expira;
  - alergia não entra mais na memória (a fonte é o cadastro), e perfis antigos
    perdem o campo ao serem lidos. "Sou alérgica a X" recebe uma resposta fixa
    pedindo para atualizar a lista de alergias no app.
- **`get_user_profile` mudou de módulo e lê mais tabelas.** Saiu de
  `venus_sdk.tools.rotina` para `venus_sdk.tools.compartilhadas` (junto do
  `get_user_allergies`), sem alias no caminho antigo, e passou a ser usado
  também pelos agentes de produto e ingrediente. A consulta faz JOIN em
  `venus.users` (campo `name`) e lê `venus.user_preferences` (campo
  `preferences`): o usuário do Postgres da API precisa de `SELECT` nas duas.
  - Antes: `from venus_sdk.tools.rotina import get_user_profile`
  - Depois: `from venus_sdk.tools.compartilhadas import get_user_profile`
- **Teto de chamadas de tool:** os especialistas de produto e de rotina param
  em 6 chamadas por tentativa (`ToolCallLimitMiddleware`); as recusadas ficam
  fora da evidência do Juiz.
- **Tools MCP filtradas:** `get_mcp_tools` aplica `com_filtro_de_injecao`, que
  descarta o retorno de tool externa que parece instrução ao sistema. A
  pergunta enviada a um agente A2A externo passa por `anonimizar_entrada`.
- Módulos movidos, com alias no caminho antigo (nada quebra):
  `venus_sdk.state` -> `venus_sdk.nodes.state` e `venus_sdk.guardrail_rules`
  -> `venus_sdk.prompts.guardrail_rules`.

### Novidades
- **Resultado do turno no estado**, para a API gravar no trace do Langfuse:
  - `juiz_decidiu_por` (tipo `DecisorJuiz`): `"regra"` quando o código deu o
    veredito sem chamar o LLM do Juiz, `"llm"` quando o veredito veio do LLM e
    `"falha_llm"` quando o LLM falhou e a resposta foi reprovada sem feedback;
  - `alertas_alergia_anexados`: quantos alertas de alergia o orquestrador
    anexou porque o texto do LLM não trazia.
- **Alerta de alergia determinístico:** depois da reescrita do LLM, o
  orquestrador cruza as alergias do cadastro com os ingredientes consultados
  e anexa o alerta em texto fixo, para o LLM não omitir nem suavizar um
  conflito. O casamento fica em `venus_sdk/alergias.py`, com sinônimos em
  português e inglês para as 18 alergias do catálogo de produção, e é a mesma
  regra do Juiz, do orquestrador e do `suggest_routine`.
- **Perfil completo:** `get_user_profile` traz o nome do cadastro e as
  preferências de valores do app (vegano, cruelty free, sem fragrância...).
  Produto e ingrediente consultam o perfil em "serve pra mim?", sem afirmar
  segurança na gestação sem dado de tool nem dizer que um produto atende a
  uma preferência de valores.
- **Governança da memória:** `{"remover": [...]}` apaga um fato que deixou de
  valer; `apagar_memoria(store, usuario_id)` (`venus_sdk.nodes.memoria`) apaga
  a memória inteira, para a exclusão de dados (LGPD); o log de auditoria diz
  quais campos mudaram, sem os valores.
- **Contexto da conversa:** o prefixo "Sobre <produto>: " vira
  `produto_em_contexto` no estado, e especialistas e Juiz recebem o histórico
  recente, para "sim, sugira" e "qual vc recomendaria?".
- Avaliações de roteamento e de alergias (`docs/avaliacao-roteamento.md` e
  `docs/avaliacao-alergias.md`) e o relatório de custo, latência e ROI
  (`docs/custo-e-roi.md`).

### Correções
- **Juiz:** a nota de cada produto é conferida em código contra as tools (id,
  nome e nota); erro reprova sem LLM com feedback exato, e cópia homônima do
  mesmo produto é aceita. Afirmar que um produto não tem o ingrediente da
  alergia exige os ingredientes daquele produto. Antes, o Juiz esgotava as
  tentativas na maioria dos turnos de produto.
- **Alergias:** casamento corrigido (fragrância e seus alérgenos,
  salicilatos, oleaginosas, sulfato só como surfactante, propilenoglicol, álcool
  graxo que não é "Álcool"). Recall 53/71 -> 71/71 e falsos 5 -> 0 nos pares
  do catálogo.
- **Roteador:** perguntas sobre o próprio Venus (conta, metodologia da nota,
  fonte dos dados, limites da IA) vão para o FAQ em vez de serem respondidas
  direto; "sim" a uma oferta de sugestão vai para o especialista de produto.
- A conversa não trava mais repetindo a ficha do produto em tela, e o
  orquestrador não oferece adicionar aos favoritos (orienta a salvar pelo
  app).
- O guardrail de entrada zera `rota`, `juiz_decidiu_por` e
  `alertas_alergia_anexados` a cada turno. Antes, numa entrada bloqueada, a
  `rota` do turno anterior voltava pelo checkpointer.
- O exemplo de chatbot segue com o histórico em memória quando o MongoDB não
  responde.
- `docs/neo4j.md`: a sincronização é manual (workflow "Sincronizar Neo4j" da
  API), não toda noite.

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
