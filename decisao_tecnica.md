# Decisão técnica: escrita no banco após a aprovação do juiz

Implementação dos commits `d3f28eb` e `5f45bc1`.

## 1. A decisão tomada, e por quê

O especialista passou a preparar uma proposta antes de qualquer gravação. O juiz avalia os dados e, se aprovar, um executor Python chama a ferramenta de escrita. Esse executor é uma função em um nó do grafo e não usa outro modelo de IA.

A autorização de escrita fica na passagem do nó `juiz` para o nó `executor`. Para seguir por esse caminho, o estado precisa conter uma proposta e `avaliacao.aprovado = true`. O juiz aprova quando recebe o veredito `apoiada` e uma pontuação de pelo menos `0,70`, dentro do intervalo válido de 0 a 1. O executor valida os argumentos novamente e confere se o comando é igual à proposta que o juiz aprovou. Se os dados forem diferentes, não chama a ferramenta.

O estado do LangGraph já passa pelos especialistas, pelo juiz e pelos demais nós. A proposta, as evidências e a avaliação agora seguem nesse estado, e uma aresta condicional decide se a execução pode continuar. Os especialistas mantêm as ferramentas de consulta, mas perderam o acesso às de escrita.

### Fluxo de antes

```text
Usuário → guardrail de entrada → roteador → especialista
                                            ↓
                                ferramenta escreve no banco
                                            ↓
                              orquestrador → guardrail de saída
                                            ↓
                                          juiz → fim
```

Quando o juiz rejeitava a resposta, a alteração no banco já estava feita. Havia também um caminho de confirmação de evento que gravava diretamente dentro de `no_agenda`.

### Fluxo de depois

```text
Usuário → guardrail de entrada → roteador → especialista prepara proposta
                                            ↓
                          orquestrador usa descrição neutra da operação
                                            ↓
                                  guardrail de saída → juiz
                                                       ├─ reprova → resposta sem escrita
                                                       └─ aprova
                                                           ├─ agenda sem confirmação
                                                           │   → apresenta proposta → aguarda usuário
                                                           │   → confirmação → nova avaliação do juiz
                                                           └─ operação autorizada → executor
                                                                                     ↓
                                                                            retorno da ferramenta
                                                                                     ↓
                                                                         resposta pública → fim
```

Todos os caminhos terminam em `finalizar_resposta`. Nas consultas, o orquestrador continua preparando o texto. Nos pedidos de escrita, uma frase neutra substitui qualquer afirmação antecipada de que o registro foi salvo. Só o retorno do executor pode confirmar o sucesso. Se o modelo repetir o contrato JSON, o código trata esse conteúdo antes de enviá-lo ao chat.

### Garantias por ferramenta

| Nome usado no enunciado | Implementação no projeto | Garantia no fluxo do chat |
|---|---|---|
| `add_transaction` | `app/tools/financeiro.py` | O executor só chama a inserção após aprovação correspondente e validação dos campos. |
| `update_transaction` | `app/tools/financeiro.py` | A atualização precisa identificar o registro e informar alterações; dados diferentes dos aprovados são recusados. A remoção financeira continua sendo atualização com valor zero e descrição de remoção. |
| `add_event` | `criar_evento`, em `app/tools/agenda.py` | A criação exige confirmação explícita do usuário e aprovação do juiz antes da inserção no MongoDB. |

Essa proteção vale para o fluxo do chat. Outro código ainda pode chamar as funções de banco diretamente. O juiz também pode aprovar uma interpretação errada; por isso, o executor verifica os campos por código além de exigir a aprovação do modelo.

## 2. As decisões que não foram adotadas, e por quê

As opções a seguir foram avaliadas tecnicamente como alternativas à implementação. Não há registro de que o grupo tenha discutido todas elas.

| Alternativa | Como funcionaria aqui | Vantagem real | Custo real | Por que ficou de fora |
|---|---|---|---|---|
| Resolver somente pelo prompt | Pedir ao especialista que espere o juiz, mantendo as ferramentas de escrita disponíveis. | Exige poucas alterações e não adiciona infraestrutura. | O ciclo interno de ferramentas do especialista ainda pode gravar antes de devolver o controle ao grafo. | O modelo continuaria podendo chamar a escrita antes do juiz. |
| Manter uma transação PostgreSQL aberta até o juiz decidir | Executar o SQL sem `commit`, compartilhar a conexão até o veredito e então fazer `commit` ou `rollback`. | Permite desfazer mudanças ainda não confirmadas dentro daquela transação. | A conexão e possíveis bloqueios ficam ocupados durante a espera pela API; as ferramentas atuais abrem, confirmam e fecham suas próprias conexões. A agenda usa outro banco. | Exigiria alterar o gerenciamento de conexões e não cobriria PostgreSQL e MongoDB com uma única transação. |
| Gravar primeiro e compensar depois | Excluir a transação inserida, restaurar os valores anteriores de uma atualização ou cancelar o evento quando o juiz reprovar. | Preserva boa parte das ferramentas atuais. | O dado fica visível antes da decisão; seria necessário guardar o estado anterior e tratar falha da compensação. Um efeito externo poderia já ter ocorrido. | Se a compensação falhasse, o registro reprovado permaneceria no banco. |
| Usar outro agente como executor | Um novo modelo receberia a aprovação e escolheria a ferramenta e seus argumentos. | Poderia interpretar operações menos estruturadas. | Acrescenta chamada de modelo, tokens, custo e risco de mudar os dados aprovados. | A função Python recebe os campos aprovados e chama a ferramenta sem reinterpretá-los. |
| Persistir propostas e usar uma fila de execução | Guardar comandos pendentes com identificador único; um trabalhador executaria apenas os aprovados. | Facilita recuperação após queda e controle persistente de duplicação. | Exige estados duráveis, processamento de fila, tratamento de concorrência e mudanças de infraestrutura. | O escopo desta alteração foi impedir a escrita antes do juiz. A fila pode ser adotada depois para recuperar execuções e controlar repetições. |

## 3. O efeito nas métricas de SRE, e como

O monitor guarda os últimos 50 turnos em memória e calcula a latência de cada um, o p95 e a taxa de erro. Os limites são 8 segundos para o p95 e 5% para a taxa de erro. Essa taxa divide a quantidade de turnos com status `erro` pelo total de turnos da janela. Bloqueios não entram no numerador. Não houve teste de carga comparando as versões; portanto, ainda não há medidas de quanto a mudança aumentou ou reduziu essas métricas.

### Efeito da solução adotada

O executor não acrescenta uma chamada de IA à latência do turno. Nos pedidos de escrita, o orquestrador produz uma frase neutra por código, sem chamar seu modelo. Se o juiz reprovar, o fluxo termina sem chamar a ferramenta de escrita. Quando ele aprova, a gravação ocorre depois da avaliação, ainda no mesmo turno. A espera pelo juiz e os tempos de banco continuam podendo ultrapassar o SLO de 8 segundos. Na agenda, a confirmação acontece em outro turno e exige nova avaliação; a latência por turno não representa o tempo total entre a primeira proposta e sua conclusão.

Os campos da proposta aumentam o conteúdo enviado ao juiz e podem afetar o consumo de tokens. O executor e o finalizador não consomem tokens. Deixar de chamar o modelo do orquestrador nas escritas pode reduzir os tokens e o custo registrados pelo coletor. Os pedidos HTTP ao JEV são feitos com `urllib`, fora dos callbacks de LangChain; portanto, tokens e custo do juiz não estão contabilizados pelo coletor atual, embora sua espera componha a latência do turno.

Se faltar a chave do juiz, ocorrer timeout ou erro HTTP, ou a resposta vier inválida, o fluxo recusa a operação. O executor também converte falhas tratadas em resultado de erro. Esses caminhos podem encerrar sem exceção para a API. A escrita fica impedida nesses casos, embora o monitor possa continuar mostrando a mesma taxa de erro.

Quanto aos status `ok`, `erro` e `bloqueado`, `executar_fluxo_assessor` começa com status `erro` e o mantém se uma exceção escapar. Quando o fluxo retorna, usa `status_do_estado`. Essa função não verifica a reprovação do juiz nem `resultado_execucao.status`; assim, uma operação recusada ou uma falha tratada de banco pode aparecer como `ok`. Ela procura etiquetas `guardrail_entrada:*` para marcar `bloqueado`, mas o nó atual informa o campo booleano `bloqueado` sem adicionar essas etiquetas. Por esse motivo, o bloqueio de entrada pode aparecer como `ok`, embora não deva ser contado como erro. Essa classificação continua pendente de correção.

### Comparação com alternativas que ficaram de fora

Manter uma transação aberta até o veredito ocuparia uma conexão durante a espera pelo juiz e poderia manter bloqueios no PostgreSQL. Com mais usuários, isso pode aumentar filas, latência e p95, além de gerar exceções por timeout. Se essas exceções escapassem, o monitor registraria `erro`; se fossem convertidas em respostas seguras, a limitação de classificação descrita acima permaneceria. Essa alternativa, por si só, não adiciona tokens.

Um agente executor adicional acrescentaria uma chamada de modelo após a aprovação, aumentando a latência do turno, os tokens e o custo. Para aparecer corretamente no painel, seu modelo também precisaria estar ligado ao coletor. Já a compensação faria duas operações de escrita quando houvesse rejeição: a original e a tentativa de desfazê-la, aumentando tempo de banco e criando mais uma possibilidade de falha.

## 4. As outras decisões

### Escopo

O executor controla `add_transaction`, `update_transaction`, `criar_evento`, `atualizar_evento` e `cancelar_evento`. Consultas continuam disponíveis aos especialistas e não passam pelo executor de escrita.

O repositório não implementa `add_google_event`. A agenda atual grava no MongoDB. A criação no Google Calendar não está coberta pela implementação nem pelos testes. Caso essa integração seja adicionada, sua chamada deverá ficar depois da aprovação, com tratamento próprio de repetição e falha externa.

`salvar_mensagem` continua fora da autorização de escrita de negócio. A função roda depois do grafo e salva a pergunta e a resposta final, inclusive quando há recusa. Assim, a conversa fica registrada mesmo que nenhuma despesa ou compromisso seja criado. O resumo e a indexação da sessão também não foram transferidos para o executor.

### Se o juiz não responder

A chamada HTTP ao juiz usa timeout de 30 segundos. Se a chamada falhar, o fluxo reprova a operação e não libera o executor. O finalizador informa que a operação não foi validada e que nenhuma alteração foi feita. O silêncio do juiz nunca autoriza a execução. A espera de até 30 segundos, porém, pode ultrapassar o SLO de latência de 8 segundos.

### Se o processo cair entre passos

| Momento da queda | O que pode permanecer |
|---|---|
| Antes de chamar a ferramenta | Nenhuma escrita de negócio dessa execução foi realizada. O checkpoint e a proposta pendente são mantidos em `MemorySaver`, em memória, e se perdem com o processo. |
| Durante uma operação PostgreSQL antes do `commit` | A transação não confirmada pode ser revertida quando a conexão termina; ainda é necessário distinguir esse caso de um `commit` concluído cuja resposta não chegou. |
| Depois do `commit` ou da inserção no MongoDB, antes da resposta | O registro pode existir mesmo que o usuário não receba a confirmação. A aprovação não torna a gravação atômica com a resposta HTTP. |
| Depois da escrita de negócio, antes ou durante `salvar_mensagem` | A despesa ou o evento pode existir sem o histórico completo daquela interação. Não há transação única entre PostgreSQL, MongoDB e a resposta do chat. |

### Repetição do mesmo pedido

Os campos de proposta, avaliação e resultado são reiniciados a cada turno. Depois da confirmação, o fluxo retira a proposta da agenda do estado pendente. Isso impede que um segundo "sim" simplesmente execute a mesma proposta novamente naquele caminho.

Ainda não há idempotência persistente. Reenviar "gastei 17 reais no almoço" pode produzir uma nova proposta aprovada e outro lançamento. Atualizações repetidas também podem ser executadas novamente. Na agenda, a consulta de conflito pode recusar um segundo evento no mesmo horário, mas não substitui uma chave única de operação nem elimina uma corrida entre requisições concorrentes.

### Evidências e limite dos testes

Os 30 testes de `tests/test_aprovacao_escrita.py` passaram na verificação da implementação. Eles usam o LangGraph real, mas simulam os modelos e as escritas. Por isso, o resultado não comprova gravações em bancos de produção.

| Operação | Evidência existente |
|---|---|
| `add_transaction` | `test_reprovacao_nao_grava` verifica zero chamadas de escrita após reprovação; `test_aprovacao_executa_depois_do_juiz` verifica uma chamada após o juiz e o identificador simulado `123`; o teste do relato de R$ 17 verifica recusa sem JSON nem confirmação falsa. |
| `update_transaction` | O teste de remoção verifica uma chamada com valor zero; o teste da ferramenta usa conexão simulada e verifica um `commit`; o teste de dados alterados verifica a recusa no executor. Ainda não há um par dedicado de recusa e aprovação com marcador único para essa operação em banco real. |
| `add_event` / `criar_evento` | O teste de agenda verifica zero chamadas antes da confirmação, uma após confirmação e aprovação e nenhuma chamada adicional no segundo "sim". Outro teste verifica zero chamadas quando a confirmação é reprovada. |

Ainda falta produzir a evidência com marcador único nas três ferramentas. Os testes usam identificadores de sessão derivados de `self.id()` e contagem de chamadas, mas os relatos e títulos não têm marcadores únicos de auditoria no banco. A contagem de chamadas simuladas não comprova que uma gravação real ocorreu exatamente uma vez.

Para verificar isso em um banco de teste, a proposta é usar marcadores distintos, como `DEC-ADD-<uuid>` no `source_text`, `DEC-UPD-<uuid>` na descrição de uma transação de teste e `DEC-EVT-<uuid>` no título do evento. Em cada ferramenta, primeiro seria registrado o estado inicial. Depois, uma reprovação forçada em ambiente controlado permitiria conferir se nenhuma inserção ocorreu ou se o registro original permaneceu intacto. Por fim, uma única execução aprovada permitiria verificar a alteração esperada e a contagem de chamadas. Na atualização, conferir os campos antes e depois, pois a contagem de linhas não comprova ausência de alteração. Esse roteiro ainda não foi executado. Mesmo que passe, será necessário testar separadamente a proteção contra reenvios futuros.

## 5. Arquivos criados e arquivos modificados

Os dois commits citados e este documento envolvem os arquivos listados a seguir. Os arquivos de rotas, API e observabilidade foram apenas consultados e permanecem sem alterações.

### Arquivos criados

- `app/executor.py`: valida comandos, exige aprovação correspondente, controla confirmação de agenda e executa somente operações permitidas.
- `app/saida.py`: converte contratos internos em texto público e filtra alegações de gravação fora do retorno do executor.
- `tests/test_aprovacao_escrita.py`: verifica ordem de execução, recusa sem escrita, confirmação, validação, falhas e apresentação da resposta.
- `decisao_tecnica.md`: documenta a decisão, alternativas, efeitos no monitor, escopo, evidências e limitações; não participa da execução do sistema.

### Arquivos modificados

- `app/graph.py`: separa proposta de execução, preserva evidências, gerencia confirmação pendente, encaminha conforme o juiz e finaliza a resposta pública antes de salvar o histórico.
- `app/juiz.py`: avalia a proposta junto com as evidências e retorna aprovação explícita, vinculada aos dados avaliados, com recusa em caso de falha.
- `app/prompts.py`: orienta os especialistas a propor ações sem declarar sucesso e o orquestrador a responder em linguagem natural, sem expor o contrato JSON.
- `app/schemas.py`: define os contratos de proposta de operação, avaliação do juiz e resultado da execução.
- `app/tools/financeiro.py`: separa a lista de ferramentas de leitura e permite atualizar o valor para zero, usado na remoção financeira prevista no projeto.
- `app/tools/agenda.py`: separa a lista de ferramentas de leitura oferecidas ao especialista, mantendo as escritas para o executor.
