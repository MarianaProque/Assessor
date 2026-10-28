"""Tool de leitura do perfil para o especialista financeiro."""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.perfil import consultar_perfil


@tool
def consultar_perfil_financeiro(consulta: str, config: RunnableConfig) -> str:
    """Consulta o perfil financeiro do usuário atual para fundamentar um conselho."""
    configuravel = (config or {}).get("configurable", {})
    user_id = configuravel.get("user_id") or configuravel.get("thread_id")
    if not user_id:
        return "Não foi possível identificar o usuário para consultar o perfil."

    perfil = consultar_perfil(user_id, consulta)
    if not perfil:
        return "PERFIL_NAO_ENCONTRADO: oriente o usuário a cadastrar o perfil na tela Perfil."

    return (
        f"Renda mensal: R$ {perfil['renda_mensal']:.2f}.\n"
        f"Objetivo: {perfil['objetivo']}.\n"
        f"Tolerância a risco: {perfil['tolerancia_risco']}.\n"
        f"Preferências semanticamente relacionadas: {perfil['preferencias']}"
    )


TOOLS_PERFIL = [consultar_perfil_financeiro]
