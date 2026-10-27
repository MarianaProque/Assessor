import sys
from pathlib import Path
from typing import Annotated, Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from app.tools.financeiro import (
    add_transaction as _add_transaction,
    daily_balance as _daily_balance,
    query_transactions as _query_transactions,
    total_balance as _total_balance,
    update_transaction as _update_transaction,
)

mcp = MCPServer(
    name="assessor-financeiro",
    version="1.0.0",
    instructions="Ferramentas financeiras do Assessor.AI...",
)

LEITURA = ToolAnnotations(readOnlyHint=True)
ESCRITA = ToolAnnotations(readOnlyHint=False, destructiveHint=False)

@mcp.tool(
    name="total_balance",
    title="Saldo total",
    description="Saldo de todo o histórico: receitas menos despesas.",
    annotations=LEITURA,
)
def total_balance() -> dict[str, Any]:
    return _total_balance.invoke({})


@mcp.tool(
    name="add_transaction",
    title="Registrar transação",
    description="Registra uma transação na tabela transactions.",
    annotations=ESCRITA,
)
def add_transaction(
    amount: float = Field(
        ...,
        description="Valor da transação (use positivo).",
    ),
    source_text: str = Field(
        ...,
        description="Texto original do usuário.",
    ),
    occurred_at: Optional[str] = Field(
        default=None,
        description="Timestamp ISO 8601; se ausente, usa NOW() no banco.",
    ),
    type_id: Optional[int] = Field(
        default=None,
        description="ID em transaction_types (1=INCOME, 2=EXPENSES, 3=TRANSFER).",
    ),
    type_name: Optional[str] = Field(
        default=None,
        description="Nome do tipo: INCOME | EXPENSES | TRANSFER.",
    ),
    category_id: Optional[int] = Field(
        default=None,
        description="FK de categories (opcional).",
    ),
    category_name: Optional[str] = Field(
        default=None,
        description="Nome da categoria (ex: comida, transporte, etc.).",
    ),
    description: Optional[str] = Field(
        default=None,
        description="Descrição (opcional).",
    ),
    payment_method: Optional[str] = Field(
        default=None,
        description="Forma de pagamento (opcional).",
    ),
) -> dict[str, Any]:
    return _add_transaction.invoke({
        "amount": amount,
        "source_text": source_text,
        "occurred_at": occurred_at,
        "type_id": type_id,
        "type_name": type_name,
        "category_id": category_id,
        "category_name": category_name,
        "description": description,
        "payment_method": payment_method,
    })

@mcp.tool(
    name="query_transactions",
    title="Consultar transações",
    description="Consulta transações na tabela transactions.",
    annotations=LEITURA,
)

def query_transactions(
    limit: int = Field(default=10, description="Número máximo de transações a retornar."),
    date: Optional[str] = Field(default=None, description="Filtrar por data no formato YYYY-MM-DD."),
    type_filter: Optional[str] = Field(default=None, description="Filtrar por tipo: INCOME | EXPENSES | TRANSFER.")
) -> dict[str, Any]:
    return _query_transactions.invoke({
        "limit": limit,
        "date": date,
        "type_filter": type_filter,
})

if __name__ == "__main__":
    from app.config import DATABASE_URL

    if not DATABASE_URL:
        print("[assessor-financeiro] DATABASE_URL ausente no .env", file=sys.stderr)

    mcp.run(transport="stdio")
