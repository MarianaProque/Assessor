"""Rota de escrita do perfil financeiro."""

from fastapi import APIRouter

from app.perfil import salvar_perfil
from app.schemas import PerfilRequest, PerfilResponse

router = APIRouter(tags=["perfil"])


@router.post("/perfil", response_model=PerfilResponse)
def salvar(requisicao: PerfilRequest) -> PerfilResponse:
    """Salva ou substitui o perfil do usuário informado pela tela Perfil."""
    return salvar_perfil(requisicao)
