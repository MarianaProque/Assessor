"""Converte contratos internos em texto público, sem confirmar escritas."""
import json
import re

FALHA_FORMATO = "Não consegui preparar uma resposta válida. Pode reformular o pedido?"
SEM_CONFIRMACAO = "Não há confirmação de gravação dessa operação."


def texto_publico(texto: str) -> str:
    texto = texto.strip()
    if texto.startswith("```"):
        bloco = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", texto, flags=re.IGNORECASE)
        if bloco:
            texto = bloco.group(1).strip()
    try:
        contrato = json.loads(texto)
    except (ValueError, TypeError):
        contrato = None
    if isinstance(contrato, dict):
        # Nunca apresenta dados, proposta, argumentos ou metadados do contrato.
        campos = ("resposta", "recomendacao", "esclarecer", "acompanhamento")
        partes = [contrato[c].strip() for c in campos
                  if isinstance(contrato.get(c), str) and contrato[c].strip()]
        texto = "\n".join(partes)
    elif contrato is not None:
        return FALHA_FORMATO
    if not texto or re.search(r'"(?:dominio|intencao|proposta|dados|acao)"\s*:', texto) or "```" in texto:
        return FALHA_FORMATO
    # Confirmações públicas são exclusivas do resultado verificado do executor.
    if re.search(
        r"\b(registrei|salvei|gravei|lancei|agendei|atualizei|cancelei|exclu[ií])\b|"
        r"\b(?:registro|transa[çc][aã]o|evento|opera[çc][aã]o)\b[^.\n]{0,120}"
        r"\b(?:conclu[ií]d[oa]|salv[oa]|gravad[oa]|registrad[oa]|agendad[oa]|atualizad[oa]|cancelad[oa]|sucesso)\b|"
        r"\b(?:foi|foram)\s+(?:salv[oa]s?|gravad[oa]s?|registrad[oa]s?|agendad[oa]s?)\b",
        texto, flags=re.IGNORECASE,
    ):
        return SEM_CONFIRMACAO
    return texto
