<!--
  Preencha as seções abaixo. Apague o que não se aplicar.
  Título do PR: siga a mesma convenção dos commits, por exemplo:
  feat: adicionar agente de resumo
  fix: corrigir carregamento do .env
-->

## O que este PR faz

<!-- Uma ou duas frases explicando a mudança, em linguagem simples. -->

## Tipo de mudança

- [ ] `feat` funcionalidade nova
- [ ] `fix` correção de bug
- [ ] `refactor` mudança interna, sem alterar comportamento
- [ ] `docs` documentação

## Evidência

<!-- Log de execução, saída do teste manual, ou link do ambiente.
     Obrigatório sempre que a mudança for observável em runtime
     (novo agente/fluxo, mudança de config, etc.). -->

## Checklist do autor

- [ ] O título segue a convenção de conventional commits
- [ ] Testei manualmente o que mudou e descrevi como reproduzir acima (ex: `python -c "from venus...."` ou execução de um exemplo em `examples/`)
- [ ] Não deixei `print()` de debug nem código comentado sem necessidade
- [ ] Não commitei nenhuma chave, senha ou URL privada (segredos ficam só no `.env`, que está no `.gitignore`)
- [ ] Todo código novo em `src/` tem type hints e docstring
- [ ] Novos agentes herdam de `BaseAgent` (`src/venus/agents/`) e novos fluxos de `BaseFlow` (`src/venus/flows/`), implementando os métodos abstratos exigidos
- [ ] Se usei uma variável de ambiente nova, ela foi adicionada em `config/settings.py` (incluindo em `OBRIGATORIAS`, se for obrigatória) e documentada
- [ ] Dependências novas foram adicionadas em `pyproject.toml`, não instaladas soltas na venv
- [ ] Adicionei ou atualizei testes em `tests/` para a lógica nova

## Notas para o revisor

<!-- Qualquer coisa que mereça atenção especial, uma dúvida em aberto,
     ou uma decisão que você gostaria de discutir. -->
