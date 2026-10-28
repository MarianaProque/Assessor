# Justificativa da prática Perfil financeiro do usuário

1. Criei os arquivos de perfil e a justificativa: app/perfil.py, app/routes/perfil.py, app/tools/perfil.py e JUSTIFICATIVA.md. Também ajustei os arquivos principais da API para conectar o perfil ao assessor.

2. O perfil entra pela tela Perfil, que envia os dados para POST /perfil. Renda, objetivo e risco vão para o MongoDB; as preferências em texto vão para o Qdrant.

3. As preferências viram uma representação de significado e são guardadas no Qdrant. Assim, o assessor entende relações como “não quero risco” e “criptomoedas”, mesmo que as palavras não sejam iguais.

4. Criei uma ferramenta: consultar_perfil_financeiro. Ela reúne os dados financeiros e as preferências relevantes em uma única consulta, suficiente para o assessor orientar a pessoa.

5. Todas as consultas usam o user_id da conversa. O MongoDB busca o perfil desse usuário e o Qdrant também filtra as preferências pelo mesmo identificador.

6. A ferramenta consulta os bancos diretamente pela própria aplicação. Isso é mais simples e evita uma chamada da API para ela mesma.

7. Porque perfil não é um assunto separado: ele apoia as recomendações do especialista financeiro. Criar outro agente tornaria o fluxo mais complicado sem necessidade.

8. O chat apenas consulta o perfil para orientar melhor. Ele não possui nenhuma ferramenta de salvar ou editar; mudanças só acontecem pela tela Perfil.