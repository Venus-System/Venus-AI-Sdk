# Arquitetura

Visão geral dos módulos do SDK Venus:

- **agents/** — implementações dos agentes de IA (cada um herda de `BaseAgent`).
- **flows/** — orquestração e pipelines que combinam múltiplos agentes (`BaseFlow`).
- **tools/** — ferramentas que os agentes podem invocar (busca, APIs externas, etc.).
- **memory/** — persistência de contexto/histórico entre execuções.
- **llm/** — clients e wrappers de integração com provedores de LLM.
- **config/** — configurações e variáveis de ambiente do SDK.
- **utils/** — funções utilitárias compartilhadas.
