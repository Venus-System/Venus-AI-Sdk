# Limites da IA e Quando Procurar um Profissional

## O Venus substitui uma avaliação dermatológica?

**Não.** O aplicativo é uma ferramenta de apoio para análise de ingredientes e compatibilidade de cosméticos, incapaz de realizar exames físicos, diagnosticar doenças ou prescrever tratamentos.

Esta informação é explícita e de fácil identificação no sistema:

- **Aviso Obrigatório:** Toda sugestão gerada pelo sistema (incluindo as respostas do chatbot e análises) carrega o aviso expresso de que não substitui um dermatologista.
- **Inteligência Clínica como Apoio:** O sistema não realiza diagnósticos médicos nem consultas; ele apenas aplica regras de compatibilidade cruzando ingredientes e perfis de usuários.

---

## Em que situações a resposta da IA recomenda buscar um profissional?

O Venus recomenda ativamente que o usuário procure um profissional de saúde/dermatologista nas seguintes situações:

- **Dúvidas Clínicas:** Quando o usuário faz perguntas com teor claramente clínico no chatbot, ele é encaminhado diretamente a um profissional.
- **Presença de Condições de Saúde e Medicamentos:** Ao identificar potenciais riscos ou contraindicações associados a condições médicas (ex: gravidez, tratamentos como uso de isotretinoína, crises de sensibilidade ou doenças de pele), o sistema aciona alertas de segurança.
- **Itens Não Mapeados na Base de Dados:** Quando o usuário informa um ingrediente ou medicamento desconhecido/fora da base, o sistema gera um aviso de cautela até que a informação passe por moderação e revisão humana.

---

## Como a IA decide o que responder? *(Baseada em dados, nunca em invenção)*

A arquitetura do Venus foi desenhada para eliminar riscos de alucinação e invenções por parte da IA. O processo de decisão segue rigorosamente os seguintes critérios:

- **A IA não é a fonte da informação, é apenas o tradutor:** O modelo de linguagem (LLM) do chatbot não gera respostas de "cabeça" ou de sua própria memória. Ele apenas consulta a base de dados determinística e reescreves as informações em linguagem natural para o usuário.
- **Motor Determinístico de Regras:** Quem toma todas as decisões de compatibilidade, pontuação e contraindicações é um motor determinístico pré-programado, e não a IA gerativa.

### Origem dos Dados e Validação Médica

- Os dados de ingredientes e segurança vêm de fontes oficiais e públicas (como PubChem, CosIng e ANVISA).
- Todas as regras médicas (como as referentes a gravidez, doenças e uso de medicamentos) passam obrigatoriamente por revisão e aprovação de um dermatologista antes de serem ativadas no sistema.
- **Ordem de Prioridade Inegociável:** Quando há conflito de critérios, a segurança sempre domina. Por exemplo, se um ingrediente bonifica um tipo de pele, mas é contraindicado para uma condição médica do usuário, o alerta de segurança ou bloqueio se sobressai.

---

## O que fazer se uma resposta parecer errada ou incompleta?

O sistema dispõe de fluxos estruturados de verificação, auditoria e curadoria contínua para lidar com inconformidades:

- **Fila de Curadoria e Moderação:** Quaisquer dados desconhecidos inseridos, informações fornecidas pela comunidade ou discrepâncias entram em uma fila de curadoria. Elas passam por moderação rígida e revisão antes de passarem a integrar a base oficial.
- **Sinalização de Mudanças (Versionamento):** Como os produtos passam por reformulações e os regulamentos científicos (ANVISA/CosIng) mudam, os scans e regras são auditáveis e versionados com data. Se a composição de um produto mudou, o escaneamento do novo rótulo aciona a atualização do cálculo.
- **Transparência e Cautela:** Se um dado for incerto (por exemplo, ao identificar materiais de embalagens sem ter certeza sobre a reciclabilidade regional ou ao ler um ingrediente desconhecido), o Venus nunca carimba uma afirmação categoricamente. Ele responderá comunicando a informação de forma cautelosa ou como uma tendência, indicando ao usuário o nível de incerteza daquele dado.