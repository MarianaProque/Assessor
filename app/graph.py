"""Fluxo LangGraph do Acessor.AI."""

import json
import operator
import re
from typing import Annotated

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, MessagesState, StateGraph

from app.guardrail import anonimizar_entrada, guardrail_entrada, guardrail_saida
from app.llms import llm, llm_rapido
from app.memory import salvar_mensagem
from app.observabilidade import abrir_turno, fechar_turno, status_do_estado
from app.prompts import (
    AGENDA_PROMPT_COMPLETO,
    FAQ_PROMPT_COMPLETO,
    FINANCEIRO_PROMPT_COMPLETO,
    ORQUESTRADOR_PROMPT_COMPLETO,
    ROUTER_PROMPT_COMPLETO,
)
from app.tools.financeiro import TOOLS_LEITURA
from app.tools.agenda import TOOLS_AGENDA_LEITURA
from app.tools.faq import faq_retriever
from app.tools.memoria import TOOLS_MEMORIA
from app.tools.perfil import TOOLS_PERFIL, consultar_perfil_financeiro
from app.juiz import no_juiz
from app.executor import executar_operacao, validar_proposta

router_agent = create_agent(model=llm_rapido, system_prompt=ROUTER_PROMPT_COMPLETO, tools=TOOLS_MEMORIA)
financeiro_agent = create_agent(
    model=llm, system_prompt=FINANCEIRO_PROMPT_COMPLETO,
    tools=TOOLS_LEITURA + TOOLS_MEMORIA + TOOLS_PERFIL,
)
agenda_agent = create_agent(
    model=llm, system_prompt=AGENDA_PROMPT_COMPLETO, tools=TOOLS_AGENDA_LEITURA + TOOLS_MEMORIA
)
orquestrador_agent = create_agent(
    model=llm_rapido, system_prompt=ORQUESTRADOR_PROMPT_COMPLETO
)
# O retriever do FAQ ainda depende de indexação externa; o agente permanece
# disponível para responder pelas instruções de sistema enquanto ela não ocorre.
faq_agent = create_agent(
    model=llm_rapido, system_prompt=FAQ_PROMPT_COMPLETO, tools=[faq_retriever]
)


class Estado(MessagesState):
    agentes_chamados: Annotated[list[str], operator.add]
    rota: str
    mapa_pii: dict[str, str]
    bloqueado: bool
    proposta: dict | None
    avaliacao: dict | None
    resultado_execucao: dict | None
    evidencias: list[str]
    proposta_pendente: dict | None
    confirmada: bool
    erro_proposta: bool


def _texto_mensagem(mensagem: object) -> str:
    conteudo = getattr(mensagem, "content", mensagem)
    return conteudo if isinstance(conteudo, str) else str(conteudo)


def no_guardrail_entrada(estado: Estado) -> dict:
    pergunta = _texto_mensagem(estado["messages"][-1])
    texto, mapa_pii = anonimizar_entrada(pergunta)
    pendente = estado.get("proposta_pendente") if _confirmacao_explicita(pergunta) else None
    resultado = guardrail_entrada(texto)
    if resultado["bloqueado"]:
        return {
            "bloqueado": True,
            "proposta_pendente": None,
            "mapa_pii": mapa_pii,
            "messages": [AIMessage(content=resultado["mensagem"])],
        }
    return {"bloqueado": False, "mapa_pii": mapa_pii, "proposta_pendente": pendente}


def decidir_pos_guardrail_entrada(estado: Estado) -> str:
    return "fim" if estado.get("bloqueado", False) else "roteador"


def no_roteador(estado: Estado, config) -> dict:
    if estado.get("proposta_pendente") and _confirmacao_explicita(_texto_mensagem(estado["messages"][-1])):
        return {"agentes_chamados": ["roteador"], "rota": "agenda"}
    saida = router_agent.invoke({"messages": list(estado["messages"])}, config=config)
    texto = _texto_mensagem(saida["messages"][-1])
    if not texto.strip().startswith("ROUTE="):
        return {"agentes_chamados": ["roteador"], "rota": "fim", "messages": [AIMessage(content=texto)]}

    rota = next(
        (linha.split("=", 1)[1].strip() for linha in texto.splitlines() if linha.startswith("ROUTE=")),
        "fim",
    )
    return {"agentes_chamados": ["roteador"], "rota": rota}


def no_financeiro(estado: Estado, config) -> dict:
    mensagens = list(estado["messages"])
    pergunta = _texto_mensagem(mensagens[-1])

    # A consulta é executada pelo servidor, com o user_id do contexto do chat.
    # Assim, o modelo não pode esquecer de chamar a tool nem informar o usuário
    # errado. A tool continua disponível ao especialista para consultas adicionais.
    perfil = consultar_perfil_financeiro.invoke({"consulta": pergunta}, config=config)
    mensagens = [SystemMessage(content=(
        "CONTEXTO DE PERFIL PRÉ-CONSULTADO:\n"
        f"{perfil}\n\n"
        "Use este contexto somente quando a pergunta exigir dados ou recomendação "
        "individualizada de perfil. Para os demais pedidos financeiros, siga o "
        "fluxo normal."
    )), *mensagens]
    saida = financeiro_agent.invoke({"messages": mensagens}, config=config)
    return _saida_especialista("financeiro", saida, len(mensagens), estado, [f"[perfil] {perfil}"])


def _json_da_mensagem(mensagem: object) -> dict | None:
    """Obtém o JSON estruturado produzido pelo especialista, se houver."""
    texto = _texto_mensagem(mensagem).strip()
    if texto.startswith(chr(96) * 3):
        texto = texto.strip(chr(96)).removeprefix("json").strip()
    try:
        valor = json.loads(texto)
    except json.JSONDecodeError:
        return None
    return valor if isinstance(valor, dict) else None


def _confirmacao_explicita(texto: str) -> bool:
    # A confirmação deve ser uma resposta inteira, não "sim, mas mude...".
    return bool(re.fullmatch(
        r"\s*(sim|confirmo|pode confirmar|pode criar|pode agendar|confirmar|pode cancelar|pode atualizar)[.!]?\s*",
        texto, flags=re.IGNORECASE,
    ))


def _saida_especialista(nome, saida, inicio, estado, evidencias=None):
    novas = saida["messages"][inicio:]
    provas = list(estado.get("evidencias") or []) + list(evidencias or [])
    provas.extend(f"[{getattr(m, 'name', 'tool')}] {_texto_mensagem(m)}"
                  for m in novas if getattr(m, "type", "") == "tool")
    ultima = saida["messages"][-1]
    contrato = _json_da_mensagem(ultima) or {}
    proposta = None
    erro = False
    escrita = contrato.get("proposta")
    intencao_escrita = contrato.get("intencao") in {
        "inserir", "atualizar", "deletar", "criar", "cancelar"
    }
    if escrita is not None:
        try:
            proposta = validar_proposta(escrita).model_dump()
            financeiro = proposta["acao"] in {"add_transaction", "update_transaction"}
            if nome not in {"financeiro", "agenda"} or financeiro != (nome == "financeiro") or contrato.get("esclarecer"):
                raise ValueError("Proposta incompatível.")
        except (ValueError, TypeError, KeyError):
            proposta = None
            erro = True
    elif intencao_escrita and not contrato.get("esclarecer"):
        erro = True
    return {
        "agentes_chamados": [nome], "messages": [ultima],
        "evidencias": provas, "proposta": proposta,
        "erro_proposta": erro, "confirmada": False,
        "proposta_pendente": None,
    }


def no_agenda(estado: Estado, config) -> dict:
    mensagens = list(estado["messages"])
    pergunta = _texto_mensagem(mensagens[-1])
    pendente = estado.get("proposta_pendente")
    if pendente and _confirmacao_explicita(pergunta):
        return {
            "agentes_chamados": ["agenda"], "proposta": pendente,
            "confirmada": True, "proposta_pendente": None,
            "evidencias": ["[proposta apresentada e confirmada pelo usuário] " + json.dumps(pendente, ensure_ascii=False)],
            "messages": [AIMessage(content="Proposta confirmada pelo usuário; aguardando avaliação para executar.")],
        }
    saida = agenda_agent.invoke({"messages": mensagens}, config=config)
    return _saida_especialista("agenda", saida, len(mensagens), estado)


def decidir_pos_juiz(estado: Estado) -> str:
    avaliacao = estado.get("avaliacao") or {}
    if not avaliacao.get("aprovado") or not estado.get("proposta"):
        return "fim"
    if estado["proposta"]["acao"] in {"criar_evento", "atualizar_evento", "cancelar_evento"} and not estado.get("confirmada"):
        return "confirmacao"
    return "executor"


def no_aguardar_confirmacao(estado: Estado) -> dict:
    proposta = estado["proposta"]
    rotulos = {"titulo": "Título", "inicio": "Início", "fim": "Fim",
               "local": "Local", "participantes": "Participantes", "lembrete": "Lembrete",
               "evento_id": "Código do evento"}
    verbos = {"criar_evento": "criar evento", "atualizar_evento": "atualizar evento",
              "cancelar_evento": "cancelar evento"}
    resumo = "\n".join(f"{rotulos[campo]}: {', '.join(valor) if isinstance(valor, list) else valor}"
                       for campo, valor in proposta["dados"].items() if valor is not None)
    return {"proposta_pendente": proposta,
            "messages": [AIMessage(content=f"Posso {verbos[proposta['acao']]} com estes dados:\n{resumo}\nConfirma essa operação?")]}


def no_executor(estado: Estado, config) -> dict:
    resultado = executar_operacao(estado["proposta"], estado.get("avaliacao") or {},
                                 config, confirmada=estado.get("confirmada", False))
    return {"agentes_chamados": ["executor"],
            "resultado_execucao": resultado.model_dump(), "proposta_pendente": None,
            "messages": [AIMessage(content=resultado.mensagem)]}


def no_faq(estado: Estado, config) -> dict:
    saida = faq_agent.invoke({"messages": list(estado["messages"])}, config=config)
    return _saida_especialista("faq", saida, len(estado["messages"]), estado)


def no_orquestrador(estado: Estado, config: RunnableConfig) -> dict:
    # Pega a última resposta do especialista (última AIMessage com conteúdo)
    ultima_especialista = ""
    for mensagem in reversed(estado["messages"]):
        if mensagem.type == "ai" and mensagem.content:
            ultima_especialista = mensagem.content
            break
    # config segue o mesmo motivo do roteador: sem ele, o modelo deste nó
    # não herda o metadata do grafo (e o registro de observabilidade perde o nó).
    saida = orquestrador_agent.invoke({
        "messages": [{"role": "human", "content": ultima_especialista}]
    }, config=config)

    return {
        "agentes_chamados": ["orquestrador"],
        "messages":        [{"role": "assistant", "content": saida["messages"][-1].text}]
    }


def no_guardrail_saida(estado: Estado) -> dict:
    resultado = guardrail_saida(
        _texto_mensagem(estado["messages"][-1]), estado.get("mapa_pii", {})
    )
    return {"messages": [AIMessage(content=resultado["conteudo"])]}


def decidir_especialista(estado: Estado) -> str:
    return estado.get("rota", "fim") if estado.get("rota") in {"financeiro", "agenda", "faq"} else "fim"


grafo = StateGraph(Estado)
grafo.add_node("guardrail_entrada", no_guardrail_entrada)
grafo.add_node("roteador", no_roteador)
grafo.add_node("financeiro", no_financeiro)
grafo.add_node("agenda", no_agenda)
grafo.add_node("faq", no_faq)
grafo.add_node("orquestrador", no_orquestrador)
grafo.add_node("guardrail_saida", no_guardrail_saida)
grafo.add_node("juiz", no_juiz)
grafo.add_node("executor", no_executor)
grafo.add_node("confirmacao", no_aguardar_confirmacao)

grafo.set_entry_point("guardrail_entrada")
grafo.add_conditional_edges("guardrail_entrada", decidir_pos_guardrail_entrada, {"roteador": "roteador", "fim": END})
grafo.add_conditional_edges("roteador", decidir_especialista, {"financeiro": "financeiro", "agenda": "agenda", "faq": "faq", "fim": END})

grafo.add_edge("financeiro",      "orquestrador")
grafo.add_edge("agenda",          "orquestrador")
grafo.add_edge("orquestrador",    "guardrail_saida")
grafo.add_edge("guardrail_saida", "juiz")
grafo.add_edge("faq",             "juiz")
grafo.add_conditional_edges("juiz", decidir_pos_juiz,
                            {"executor": "executor", "confirmacao": "confirmacao", "fim": END})
grafo.add_edge("executor", END)
grafo.add_edge("confirmacao", END)

fluxo_agentes = grafo.compile(checkpointer=MemorySaver())


def executar_fluxo_assessor(
    pergunta_usuario: str, session_id: str, user_id: str = "usuario_teste"
) -> tuple[str, list[str]]:
    pergunta_usuario_anonimizado, _ = anonimizar_entrada(pergunta_usuario)
    estado_inicial = {
        "messages":         [{"role": "human", "content": pergunta_usuario}],
        "agentes_chamados": [],
        "rota":             "",
        "mapa_pii":         {},
        "session_id":       session_id,
        "proposta": None,
        "avaliacao": None,
        "resultado_execucao": None,
        "evidencias": [],
        "confirmada": False,
        "erro_proposta": False,

    }

    # O registro abre antes do invoke e fecha no finally, inclusive quando
    # o grafo levanta. A rota /monitor só lê o que ficou anotado.
    turno_id = abrir_turno()
    status = "erro"
    rota = ""
    try:
        estado_final = fluxo_agentes.invoke(
            estado_inicial,
            config={"configurable": {"thread_id": session_id, "user_id": user_id}},
        )

        resposta = estado_final["messages"][-1].text

        # Salva no MongoDB após cada interação
        salvar_mensagem(session_id, "human",     pergunta_usuario_anonimizado, user_id=user_id)
        salvar_mensagem(session_id, "assistant", resposta, user_id=user_id)

        status = status_do_estado(estado_final)
        rota = estado_final.get("rota") or ""
        agentes = estado_final.get("agentes_chamados", [])
        return resposta, agentes
    finally:
        fechar_turno(turno_id, session_id=session_id, rota=rota, status=status)
