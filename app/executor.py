"""Executa somente comandos iguais à proposta aprovada pelo juiz."""
from datetime import datetime
from math import isfinite

from app.schemas import AvaliacaoJuiz, PropostaOperacao, ResultadoExecucao
from app.tools.financeiro import add_transaction, update_transaction
from app.tools.agenda import criar_evento, atualizar_evento, cancelar_evento

OPERACOES = {tool.name: tool for tool in (
    add_transaction, update_transaction, criar_evento, atualizar_evento, cancelar_evento
)}


def validar_proposta(valor: dict) -> PropostaOperacao:
    proposta = PropostaOperacao.model_validate(valor)
    tool = OPERACOES[proposta.acao]
    schema = tool.args_schema
    permitidos = set(schema.model_fields)
    if set(proposta.dados) - permitidos:
        raise ValueError("Campos desconhecidos na operação.")
    dados = schema.model_validate(proposta.dados).model_dump(exclude_unset=True)
    for campo in ("titulo", "evento_id", "source_text"):
        if campo in dados and not dados[campo].strip():
            raise ValueError("Texto obrigatório vazio.")
    if proposta.acao == "atualizar_evento":
        if not any(valor is not None for campo, valor in dados.items() if campo != "evento_id"):
            raise ValueError("Nenhuma alteração informada.")
    if "amount" in dados:
        amount = dados["amount"]
        if amount is None or not isfinite(amount) or amount < 0 or (proposta.acao == "add_transaction" and amount == 0):
            raise ValueError("Valor da transação inválido.")
    if proposta.acao == "update_transaction":
        if not dados.get("id") and not (dados.get("match_text") and dados.get("date_local")):
            raise ValueError("Informe o registro que será atualizado.")
        campos = set(dados) - {"id", "match_text", "date_local"}
        if not any(dados[campo] is not None for campo in campos):
            raise ValueError("Nenhuma alteração informada.")
    for campo in ("inicio", "fim", "occurred_at"):
        if dados.get(campo):
            data = datetime.fromisoformat(dados[campo])
            if data.tzinfo is None:
                raise ValueError("Informe o fuso horário.")
    if dados.get("inicio") and dados.get("fim"):
        if datetime.fromisoformat(dados["fim"]) <= datetime.fromisoformat(dados["inicio"]):
            raise ValueError("O fim deve ser posterior ao início.")
    return PropostaOperacao(acao=proposta.acao, dados=dados)


def executar_operacao(proposta: dict, avaliacao: dict, config, *, confirmada=False) -> ResultadoExecucao:
    try:
        comando = validar_proposta(proposta)
        decisao = AvaliacaoJuiz.model_validate(avaliacao)
        if not decisao.aprovado or decisao.proposta != comando:
            raise ValueError("Operação sem aprovação correspondente.")
        if comando.acao in {"criar_evento", "atualizar_evento", "cancelar_evento"} and not confirmada:
            raise ValueError("A operação de agenda aguarda confirmação.")
        retorno = OPERACOES[comando.acao].invoke(comando.dados, config=config)
        if retorno.get("status") != "ok":
            status = "conflict" if retorno.get("status") == "conflict" else "error"
            return ResultadoExecucao(status=status, mensagem="A operação não foi concluída. Verifique os dados ou conflitos e tente novamente.")
        if retorno.get("rows_affected") == 0:
            return ResultadoExecucao(status="error", mensagem="Nenhum registro foi alterado.")
        mensagens = {
            "add_transaction": "Transação registrada com sucesso.",
            "update_transaction": "Transação atualizada com sucesso.",
            "criar_evento": "Evento criado com sucesso.",
            "atualizar_evento": "Evento atualizado com sucesso.",
            "cancelar_evento": "Evento cancelado com sucesso.",
        }
        registro_id = retorno.get("id") or retorno.get("evento_id") or retorno.get("evento", {}).get("_id")
        return ResultadoExecucao(status="ok", mensagem=mensagens[comando.acao], registro_id=registro_id)
    except Exception:
        # Não expõe exceções do banco ou credenciais na resposta pública.
        return ResultadoExecucao(status="error", mensagem="Não foi possível executar a operação.")
