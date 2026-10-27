from typing import Literal

from pydantic import BaseModel, Field, field_validator

class ChatRequest(BaseModel):
    """O que o navegador envia no POST /chat."""
    session_id: str = Field(..., examples=["550e8400-e29b-41d4-a716-446655440000"])
    user_id: str = Field(
        default="usuario_teste",
        description="Identificador estável do usuário entre sessões.",
        examples=["usuario_teste"],
    )
    pergunta:   str = Field(..., min_length=1, examples=["gastei 50 reais no mercado"])


class ChatResponse(BaseModel):
    """O que a API devolve no POST /chat."""
    resposta:         str
    agentes_chamados: list[str] = Field(default_factory=list)


class PerfilRequest(BaseModel):
    """Contrato de escrita consumido pela tela Perfil."""

    user_id: str = Field(..., min_length=1)
    renda_mensal: float = Field(..., gt=0)
    objetivo: str = Field(..., min_length=1)
    tolerancia_risco: Literal["baixa", "media", "alta"]
    preferencias: str = Field(..., min_length=1)

    @field_validator("user_id", "objetivo", "preferencias")
    @classmethod
    def campos_texto_nao_vazios(cls, valor: str) -> str:
        valor = valor.strip()
        if not valor:
            raise ValueError("Este campo é obrigatório.")
        return valor


class PerfilResponse(BaseModel):
    """Perfil devolvido após um salvamento bem-sucedido."""

    user_id: str
    renda_mensal: float
    objetivo: str
    tolerancia_risco: Literal["baixa", "media", "alta"]
    preferencias: str


class SessionResponse(BaseModel):
    """Ainda não é usado — é do Passo 6 da Etapa 3."""
    session_id: str
    resumo:     str | None = None


class HealthCheck(BaseModel):
    """Resultado de uma dependência ou requisito da aplicação."""

    status: Literal["ok", "not_configured", "unavailable"]
    message: str


class HealthResponse(BaseModel):
    """Contrato do endpoint GET /health."""

    status: Literal["ok", "degraded"]
    checks: dict[str, HealthCheck]
