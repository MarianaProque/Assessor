"""Persistência do perfil financeiro e das preferências semânticas."""

import uuid

from pymongo import ReturnDocument
from qdrant_client import models

from app.memory import get_db
from app.schemas import PerfilRequest, PerfilResponse
from app.vectorstore import COLLECTION_PERFIL, garantir_collections, gerar_embedding, qdrant

_indice_criado = False


def _get_col_perfis():
    """Obtém a collection de dados estruturados, com um perfil por usuário."""
    global _indice_criado
    collection = get_db()["perfis"]
    if not _indice_criado:
        collection.create_index("user_id", unique=True)
        _indice_criado = True
    return collection


def _id_preferencias(user_id: str) -> str:
    """Mantém um único ponto de preferências por usuário no Qdrant."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"perfil-preferencias:{user_id}"))


def salvar_perfil(perfil: PerfilRequest) -> PerfilResponse:
    """Substitui o perfil estruturado e as preferências do mesmo usuário."""
    dados = perfil.model_dump()
    estruturado = {
        "user_id": dados["user_id"],
        "renda_mensal": dados["renda_mensal"],
        "objetivo": dados["objetivo"],
        "tolerancia_risco": dados["tolerancia_risco"],
    }
    _get_col_perfis().find_one_and_replace(
        {"user_id": dados["user_id"]}, estruturado, upsert=True,
        return_document=ReturnDocument.AFTER,
    )

    garantir_collections()
    qdrant.upsert(
        collection_name=COLLECTION_PERFIL,
        points=[models.PointStruct(
            id=_id_preferencias(dados["user_id"]),
            vector=gerar_embedding(dados["preferencias"]),
            payload={"user_id": dados["user_id"], "preferencias": dados["preferencias"]},
        )],
        wait=True,
    )
    return PerfilResponse(**dados)


def consultar_perfil(user_id: str, consulta: str) -> dict | None:
    """Lê o perfil e recupera a preferência semanticamente relacionada."""
    estruturado = _get_col_perfis().find_one({"user_id": user_id}, {"_id": 0})
    if not estruturado:
        return None

    garantir_collections()
    resultados = qdrant.query_points(
        collection_name=COLLECTION_PERFIL,
        query=gerar_embedding(consulta),
        query_filter=models.Filter(must=[models.FieldCondition(
            key="user_id", match=models.MatchValue(value=user_id),
        )]),
        limit=1,
        with_payload=True,
    )
    preferencias = ""
    if resultados.points:
        preferencias = str(resultados.points[0].payload.get("preferencias", ""))
    return {**estruturado, "preferencias": preferencias}
