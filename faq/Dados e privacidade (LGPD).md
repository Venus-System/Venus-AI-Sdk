# Dados e privacidade (LGPD)

## Quais dados o Venus coleta?

O Venus prevê a coleta de informações necessárias para montar o perfil e personalizar as recomendações.

Entre elas estão:

- tipo de pele;
- objetivo do usuário;
- sensibilidade;
- fototipo;
- condições relevantes;
- alergias;
- medicamentos;
- idade;
- CEP;
- preferências, como cheiro, textura e marca;
- fatores temporários, quando aplicáveis;
- informações relacionadas aos produtos escaneados;
- avaliações e contribuições feitas pelo usuário.

No cadastro inicial, o projeto prevê uma triagem com aproximadamente 6 a 8 perguntas essenciais, podendo posteriormente enriquecer o perfil conforme o usuário utiliza o aplicativo.

Também existe um histórico técnico dos scans: cada análise é registrada com data e versão e pode guardar o perfil utilizado naquela análise. Isso permite, por exemplo, entender posteriormente por que determinado resultado foi produzido.

---

## Como esses dados são usados para gerar recomendações?

A lógica teórica do Venus pode ser resumida assim:

> Perfil do usuário + informações do produto → motor de regras → recomendação

O sistema divide os fatores em três grupos:

| Tipo | Função |
| :--- | :--- |
| **Filtro duro** | Elimina produtos incompatíveis |
| **Match clínico** | Calcula a adequação ao perfil |
| **Preferência** | Reordena os produtos de acordo com gostos |

**Por exemplo:**

- Alergia declarada → filtro → produto eliminado
- Tipo de pele + objetivo → match clínico → adequação calculada
- Preferência por determinada textura → preferência → produtos reorganizados

Essa distinção é importante porque uma preferência não deveria ter o mesmo peso de uma restrição de segurança.

No sistema de recomendação, primeiro são eliminados produtos proibidos; depois ocorre a pontuação de adequação; por último, as preferências ajudam a ordenar os resultados.

---

## Como editar ou apagar dados do perfil?

> Perfil → preferências

E então o usuário poderia alterar, por exemplo:

- tipo de pele;
- objetivo;
- preferências;
- alergias;
- medicamentos;
- condições;
- fatores temporários.

Os fatores temporários possuem ainda uma característica especial: têm data de ativação, expiração e estado. Quando estão próximos de expirar, o Venus pode perguntar ao usuário se aquela informação ainda é válida.

Além disso, quando o perfil muda, o sistema invalida o cache de recomendações daquele usuário e recalcula os resultados posteriormente.

### Como apagar:

> Perfil → botão excluir conta