\# Vênus — Análise e Recomendação de Cosméticos



\## O que é o app e o que ele faz



O \*\*Vênus\*\* é um aplicativo de análise e recomendação de produtos de cosméticos e cuidados pessoais. O funcionamento combina três elementos principais:



\- \*\*Análise do produto:\*\* onde ele identifica cada ingrediente e características do produto.

\- \*\*Perfil do usuário:\*\* é aqui onde o usuário vai informar suas preferências e necessidades, como tipo de pele, sensibilidade, alergias, fototipo e condições.

\- \*\*Recomendação personalizada:\*\* a partir da análise do produto, o sistema cruza os dados do produto com o perfil do usuário para determinar quais produtos são mais adequados.



\### Revisão



O sistema separa as informações do usuário em três níveis:



1\. \*\*Filtro duro:\*\* elimina os produtos incompatíveis dos tópicos de alergia, vegano, orçamento e categoria.

2\. \*\*Match clínico:\*\* pontua a adequação do produto usando como base o tipo de pele, objetivo, fototipo e condições.

3\. \*\*Preferências:\*\* servem para ordenar as opções de acordo com cheiro, textura e marca.



O Vênus pode também utilizar fatores temporários, como determinados estados ou situações que expiram, evitando que uma condição temporária fique permanentemente no perfil.



\---



\## Como funciona o scan de um produto



O fluxo depende de como o produto é identificado:



\- \*\*Código de barras → Catálogo → Análise:\*\* Se o código de barras já estiver cadastrado, o Vênus consulta seu catálogo e pode retornar o produto diretamente.

\- \*\*Foto → OCR → INCI → Confirmação → Análise:\*\* Quando o produto não está cadastrado, o usuário fotografa o rótulo. O OCR transforma a imagem em texto. Depois, o sistema normaliza os nomes dos ingredientes para o padrão INCI, e o usuário pode corrigir eventuais erros antes da análise.



\*\*Depois disso:\*\*



> Ingredientes → Atributos → Motor de regras → Score → Resultado



O sistema consulta a ontologia de ingredientes, cruza as informações com o perfil do usuário e apresenta a pontuação, ingredientes críticos, alertas de segurança e possíveis alternativas.



\*Um detalhe importante:\* O OCR não é responsável por descobrir tudo sobre a embalagem. Ele lê o rótulo e a composição, enquanto informações como embalagem, cheiro, textura e algumas certificações precisam de outras fontes.



\---



\## Como o score funciona



\### Score geral do produto

É aquilo que pode ser calculated independentemente de quem está usando o aplicativo.



Por exemplo, informações objetivas relacionadas aos ingredientes e seus atributos podem ser pré-calculadas quando o produto entra no catálogo. O documento chama esses dados de \*informações iguais para todo mundo\*, como ingredientes, flags e scores objetivos.



> Produto → Características objetivas → Score geral



\### Score personalizado

Depois o Vênus pega essas características e cruza com o perfil daquele usuário.



O motor de match clínico pode alterar os pesos de acordo com o perfil. Portanto, o mesmo produto pode apresentar resultados diferentes para pessoas diferentes.



\*\*Lógica geral:\*\*



> Score personalizado = Características do produto + Perfil do usuário + Regras de compatibilidade



\*\*Hierarquia:\*\*

\- \*\*Filtro duro:\*\* elimina o que não pode ser recomendado, como um produto incompatível com uma alergia declarada.

\- \*\*Match clínico:\*\* calcula o quanto o produto é adequado.

\- \*\*Preferências:\*\* reorganiza os resultados de acordo com gostos pessoais, como cheiro, textura ou marca.



\---



\## Significado dos selos



Os selos funcionam como informações adicionais sobre o produto, mas é importante não misturar isso com o score.



Entre os fatores previstos no sistema estão informações como vegano, além de características e preferências relacionadas à marca. O veganismo, especificamente, pode funcionar como um filtro rígido quando o usuário declarar essa restrição.



\- \*\*Cruelty-free:\*\* indica uma característica relacionada à política de testes em animais da marca/produto.

\- \*\*Vegano:\*\* indica que o produto atende ao critério vegano utilizado pelo Vênus. No sistema, essa informação pode ser usada como filtro quando o usuário declarar que isso é uma exigência.

\- \*\*Marca brasileira:\*\* identifica a origem/nacionalidade da marca.

