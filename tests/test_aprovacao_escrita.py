"""Testes locais: LangGraph real, modelos, rede e bancos simulados."""
import importlib
import json
import sys
import types
import unittest
from copy import deepcopy
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def modulo(nome, **atributos):
    return types.SimpleNamespace(**atributos)


falsos = {
    'app.memory': modulo('memory', get_db=MagicMock(), salvar_mensagem=MagicMock()),
    'app.llms': modulo('llms', llm=MagicMock(), llm_rapido=MagicMock()),
    'app.guardrail': modulo('guardrail',
        anonimizar_entrada=lambda texto: (texto, {}),
        guardrail_entrada=lambda texto: {'bloqueado': False},
        guardrail_saida=lambda texto, mapa: {'conteudo': texto}),
    'app.tools.faq': modulo('faq', faq_retriever=MagicMock()),
    'app.tools.memoria': modulo('memoria', TOOLS_MEMORIA=[]),
    'app.tools.perfil': modulo('perfil', TOOLS_PERFIL=[], consultar_perfil_financeiro=MagicMock()),
    'app.observabilidade': modulo('obs', abrir_turno=lambda: 'turno',
        fechar_turno=MagicMock(), status_do_estado=lambda estado: 'ok'),
}
# Mantém imports transitivos carregados (extensões nativas não podem ser reimportadas).
originais = {nome: sys.modules.get(nome) for nome in falsos}
sys.modules.update(falsos)
with patch('langchain.agents.create_agent', side_effect=lambda **kw: MagicMock()) as fabrica:
    graph = importlib.import_module('app.graph')
    executor = importlib.import_module('app.executor')
    juiz = importlib.import_module('app.juiz')
    ferramentas_expostas = [chamada.kwargs.get('tools', []) for chamada in fabrica.call_args_list]

for nome, original in originais.items():
    if original is None:
        sys.modules.pop(nome, None)
    else:
        sys.modules[nome] = original

JULGAR_ORIGINAL = juiz._julgar_jev

FIN = {'acao': 'add_transaction', 'dados': {'amount': 50, 'source_text': 'gastei 50 no almoço',
       'type_name': 'EXPENSES', 'category_name': 'comida'}}
AGENDA = {'acao': 'criar_evento', 'dados': {'titulo': 'Reunião',
          'inicio': '2026-10-30T14:00:00-03:00', 'fim': '2026-10-30T15:00:00-03:00'}}


def decisao(proposta, aprovado=True):
    return {'aprovado': aprovado, 'motivo': 'teste', 'pontuacao': 0.9,
            'proposta': executor.validar_proposta(proposta).model_dump()}


class FluxoTest(unittest.TestCase):
    def setUp(self):
        self.escritas = {nome: MagicMock(return_value=None) for nome in executor.OPERACOES}
        for ferramenta in self.escritas.values():
            ferramenta.invoke.return_value = {'status': 'ok', 'id': 123}
        self.aprovar = True
        self.ordem = []
        for nome, ferramenta in self.escritas.items():
            ferramenta.args_schema = executor.OPERACOES[nome].args_schema
        self.patchers = [patch.dict(executor.OPERACOES, self.escritas)]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)
        graph.consultar_perfil_financeiro.invoke.return_value = 'Perfil consultado'
        graph.router_agent.invoke.side_effect = lambda entrada, **kw: {
            'messages': [*entrada['messages'], AIMessage(content='ROUTE=' + self.rota)]}
        for nome in ('financeiro', 'agenda', 'faq'):
            getattr(graph, nome + '_agent').invoke.side_effect = self.especialista
        graph.orquestrador_agent.invoke.side_effect = lambda entrada, **kw: {
            'messages': [AIMessage(content=entrada['messages'][-1]['content'])]}
        self.julgamento = patch.object(juiz, '_julgar_jev', side_effect=self.julgar)
        self.julgamento.start()
        self.addCleanup(self.julgamento.stop)
        from langgraph.checkpoint.memory import MemorySaver
        self.fluxo = graph.grafo.compile(checkpointer=MemorySaver())
        self.proposta = deepcopy(FIN)
        self.rota = 'financeiro'
        self.esclarecer = False

    def especialista(self, entrada, **kw):
        contrato = {'dominio': self.rota, 'intencao': 'inserir' if self.rota == 'financeiro' else 'criar',
                    'resposta': 'Proponho realizar a operação.', 'recomendacao': ''}
        if self.proposta:
            contrato['proposta'] = self.proposta
        else:
            contrato['intencao'] = 'consultar'
        if self.esclarecer:
            contrato['intencao'] = 'criar'
            contrato['esclarecer'] = 'Qual horário?'
        return {'messages': [*entrada['messages'],
            ToolMessage(content='resultado da consulta', tool_call_id='consulta', name='consulta'),
            AIMessage(content=json.dumps(contrato))]}

    def julgar(self, pergunta, resposta, evidencia):
        self.ordem.append('juiz')
        self.evidencia = evidencia
        return juiz._publicar(resposta if self.aprovar else juiz.FRASE_SEGURA,
                              'teste', resposta, aprovado=self.aprovar, pontuacao=0.9)

    def turno(self, texto='gastei 50 no almoço'):
        return self.fluxo.invoke({'messages': [HumanMessage(content=texto)],
            'agentes_chamados': [], 'rota': '', 'mapa_pii': {}, 'proposta': None,
            'avaliacao': None, 'resultado_execucao': None, 'evidencias': [],
            'confirmada': False, 'erro_proposta': False},
            config={'configurable': {'thread_id': self.id(), 'user_id': 'teste'}})

    def total_escritas(self):
        return sum(t.invoke.call_count for t in self.escritas.values())

    def test_especialistas_nao_recebem_tools_de_escrita(self):
        nomes = {getattr(tool, 'name', '') for grupo in ferramentas_expostas for tool in grupo}
        self.assertFalse(nomes & executor.OPERACOES.keys())

    def test_aprovacao_executa_depois_do_juiz(self):
        def gravar(*args, **kwargs):
            self.assertEqual(self.ordem, ['juiz'])
            return {'status': 'ok', 'id': 123}
        self.escritas['add_transaction'].invoke.side_effect = gravar
        resultado = self.turno()
        self.assertEqual(self.total_escritas(), 1)
        self.assertEqual(resultado['resultado_execucao']['registro_id'], 123)
        self.assertIn('registrada com sucesso', resultado['messages'][-1].content)
        self.assertIn('resultado da consulta', self.evidencia)
        self.assertIn('Perfil consultado', self.evidencia)

    def test_reprovacao_nao_grava(self):
        self.aprovar = False
        resultado = self.turno()
        self.assertEqual(self.total_escritas(), 0)
        self.assertEqual(resultado['messages'][-1].content, juiz.FRASE_SEGURA)

    def test_erro_juiz_nao_grava(self):
        juiz._julgar_jev.side_effect = TimeoutError
        resultado = self.turno()
        self.assertFalse(resultado['avaliacao']['aprovado'])
        self.assertEqual(self.total_escritas(), 0)

    def test_proposta_invalida_nao_grava(self):
        for dados in ({'amount': -1}, {'amount': 'invalido'}, {'amount': 0}, {'extra': True}):
            with self.subTest(dados=dados):
                self.proposta = deepcopy(FIN)
                self.proposta['dados'].update(dados)
                resultado = self.turno()
                self.assertFalse(resultado['avaliacao']['aprovado'])
                self.assertEqual(self.total_escritas(), 0)

    def test_agenda_aguarda_confirmacao_e_reavalia(self):
        self.rota, self.proposta = 'agenda', deepcopy(AGENDA)
        primeiro = self.turno('marque uma reunião amanhã às 14h')
        self.assertEqual(self.total_escritas(), 0)
        self.assertIsNotNone(primeiro['proposta_pendente'])
        segundo = self.turno('sim')
        self.assertEqual(self.total_escritas(), 1)
        self.assertEqual(len(self.ordem), 2)
        self.assertIsNone(segundo['proposta_pendente'])
        self.assertIn('criado com sucesso', segundo['messages'][-1].content)
        self.turno('sim')
        self.assertEqual(self.total_escritas(), 1)

    def test_confirmacao_reprovada_nao_grava(self):
        self.rota, self.proposta = 'agenda', deepcopy(AGENDA)
        self.turno('marque uma reunião')
        self.aprovar = False
        resultado = self.turno('confirmo')
        self.assertEqual(self.total_escritas(), 0)
        self.assertIsNone(resultado['proposta_pendente'])

    def test_confirmacao_com_alteracao_nao_executa_proposta_anterior(self):
        self.rota, self.proposta = 'agenda', deepcopy(AGENDA)
        self.turno('marque uma reunião')
        self.proposta['dados']['titulo'] = 'Outra reunião'
        resultado = self.turno('sim, mas mude o título')
        self.assertEqual(self.total_escritas(), 0)
        self.assertFalse(resultado['confirmada'])
        self.assertEqual(resultado['proposta_pendente']['dados']['titulo'], 'Outra reunião')

    def test_consulta_e_esclarecimento_nao_gravam(self):
        self.proposta = None
        for esclarecer in (False, True):
            self.esclarecer = esclarecer
            resultado = self.turno('qual horário?')
            self.assertIsNone(resultado['resultado_execucao'])
        self.assertEqual(self.total_escritas(), 0)

    def test_aprovacao_anterior_nao_e_reutilizada(self):
        self.turno()
        self.aprovar = False
        resultado = self.turno('gastei 100 no almoço')
        self.assertEqual(self.total_escritas(), 1)
        self.assertIsNone(resultado['resultado_execucao'])
        self.assertFalse(resultado['avaliacao']['aprovado'])

    def test_banco_erro_conflito_excecao_nao_confirma_sucesso(self):
        ferramenta = self.escritas['add_transaction'].invoke
        for retorno in ({'status': 'error'}, {'status': 'conflict'}, {'status': 'ok', 'rows_affected': 0}):
            ferramenta.return_value = retorno
            resultado = self.turno()
            self.assertNotEqual(resultado['resultado_execucao']['status'], 'ok')
            self.assertNotIn('sucesso', resultado['messages'][-1].content)
        ferramenta.side_effect = RuntimeError('erro interno confidencial')
        resultado = self.turno()
        self.assertNotIn('confidencial', resultado['messages'][-1].content)

    def test_executor_rejeita_dados_diferentes_dos_aprovados(self):
        alterada = deepcopy(FIN)
        alterada['dados']['amount'] = 500
        resultado = executor.executar_operacao(alterada, decisao(FIN), {})
        self.assertEqual(resultado.status, 'error')
        self.assertEqual(self.total_escritas(), 0)

    def test_executor_exige_confirmacao_para_todas_escritas_agenda(self):
        propostas = [AGENDA,
            {'acao': 'atualizar_evento', 'dados': {'evento_id': 'abc', 'titulo': 'Novo título'}},
            {'acao': 'cancelar_evento', 'dados': {'evento_id': 'abc'}}]
        for proposta in propostas:
            resultado = executor.executar_operacao(proposta, decisao(proposta), {})
            self.assertEqual(resultado.status, 'error')
        self.assertEqual(self.total_escritas(), 0)

    def test_remocao_financeira_passa_valor_zero(self):
        self.proposta = {'acao': 'update_transaction', 'dados': {'id': 123,
            'amount': 0, 'description': 'Removido pelo usuário'}}
        self.turno('remova o lançamento 123')
        self.escritas['update_transaction'].invoke.assert_called_once()
        self.assertEqual(self.escritas['update_transaction'].invoke.call_args.args[0]['amount'], 0)

    def test_datas_invalidas_nao_sao_aceitas(self):
        for inicio, fim in [('invalido', 'invalido'),
                            ('2026-10-30T14:00:00', '2026-10-30T15:00:00'),
                            ('2026-10-30T16:00:00-03:00', '2026-10-30T15:00:00-03:00')]:
            proposta = deepcopy(AGENDA)
            proposta['dados'].update(inicio=inicio, fim=fim)
            with self.assertRaises(ValueError):
                executor.validar_proposta(proposta)

    def test_sem_chave_nao_aprova(self):
        original = JULGAR_ORIGINAL
        with patch.object(juiz, 'TYPESAFE_API_KEY', None):
            # Recupera a implementação original, substituída somente pelo teste de fluxo.
            resultado = original('pergunta', 'resposta', 'evidencia')
        self.assertFalse(resultado['avaliacao']['aprovado'])

    def test_criterio_real_do_juiz(self):
        for escolha, nota, aprovado in [('apoiada', .70, True), ('apoiada', .69, False),
                                        ('parcial', .99, False), ('sem_apoio', .99, False),
                                        ('apoiada', 1.1, False), ('apoiada', float('nan'), False)]:
            with self.subTest(escolha=escolha, nota=nota):
                resposta_http = MagicMock()
                resposta_http.__enter__.return_value.read.return_value = json.dumps({
                    'answers': {'veredito': {'choice': escolha}, 'apoiada': {'noul': nota}}
                }).encode()
                with patch.object(juiz, 'TYPESAFE_API_KEY', 'chave-simulada'), patch.object(
                        juiz.urllib.request, 'urlopen', return_value=resposta_http):
                    resultado = JULGAR_ORIGINAL('pergunta', 'resposta', 'evidencia')
                self.assertEqual(resultado['avaliacao']['aprovado'], aprovado)

    def test_resposta_invalida_do_juiz_nao_grava(self):
        juiz._julgar_jev.side_effect = lambda *args: {'avaliacao': {'aprovado': True}}
        resultado = self.turno()
        self.assertFalse(resultado['avaliacao']['aprovado'])
        self.assertEqual(self.total_escritas(), 0)

    def test_historico_recebe_somente_a_resposta_final(self):
        self.aprovar = False
        graph.salvar_mensagem.reset_mock()
        with patch.object(graph, 'fluxo_agentes', self.fluxo):
            resposta, _ = graph.executar_fluxo_assessor('gastei 50 no almoço', 'sessao-teste', 'usuario')
        self.assertEqual(resposta, juiz.FRASE_SEGURA)
        self.assertEqual(graph.salvar_mensagem.call_count, 2)
        self.assertEqual(graph.salvar_mensagem.call_args.args[2], juiz.FRASE_SEGURA)
        self.assertEqual(self.total_escritas(), 0)

    def test_atualizacao_e_cancelamento_agenda_confirmados(self):
        self.rota = 'agenda'
        for acao, dados in [
            ('atualizar_evento', {'evento_id': 'abc', 'titulo': 'Título novo'}),
            ('cancelar_evento', {'evento_id': 'abc'})]:
            with self.subTest(acao=acao):
                self.proposta = {'acao': acao, 'dados': dados}
                self.turno('altere o evento')
                self.escritas[acao].invoke.assert_not_called()
                resultado = self.turno('confirmo')
                self.escritas[acao].invoke.assert_called_once()
                self.assertEqual(resultado['resultado_execucao']['status'], 'ok')

    def test_confirmacao_antiga_descartada_apos_outro_assunto(self):
        self.rota, self.proposta = 'agenda', deepcopy(AGENDA)
        self.turno('marque uma reunião')
        self.rota, self.proposta = 'financeiro', None
        self.turno('qual meu saldo?')
        resultado = self.turno('sim')
        self.assertIsNone(resultado['proposta_pendente'])
        self.assertEqual(self.total_escritas(), 0)

    def test_tool_financeira_aceita_zerar_valor(self):
        financeiro = importlib.import_module('app.tools.financeiro')
        conexao = MagicMock()
        cursor = conexao.cursor.return_value
        cursor.rowcount = 1
        cursor.fetchone.return_value = None
        with patch.object(financeiro, 'get_conn', return_value=conexao):
            resultado = financeiro.update_transaction.func(id=123, amount=0)
        self.assertEqual(resultado['status'], 'ok')
        conexao.commit.assert_called_once()
        argumentos_update = cursor.execute.call_args_list[0].args
        self.assertIn('UPDATE transactions', argumentos_update[0])
        self.assertEqual(argumentos_update[1], [0, 123])


if __name__ == '__main__':
    unittest.main()
