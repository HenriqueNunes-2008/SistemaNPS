import json

from fastapi import APIRouter, HTTPException
from app.services.processo_repository import ProcessoRepository
from app.services.processo_service import ProcessoService
from app.services.supabase_client import supabase

router = APIRouter(prefix="/api/processos", tags=["Processos"])


@router.get("/ultimo-em-andamento")
def obter_ultimo_processo_em_andamento():
    res = (
        supabase
        .table("processos")
        .select("codigo,project_token,status,atualizado_em,criado_em")
        .order("atualizado_em", desc=True)
        .order("criado_em", desc=True)
        .limit(30)
        .execute()
    )

    processos = res.data or []
    for processo in processos:
        status = str(processo.get("status") or "").strip().lower()
        identifier = processo.get("project_token") or processo.get("codigo")
        if status != "finalizado" and identifier:
            return {
                "processo_id": identifier,
                "project_token": processo.get("project_token"),
            }

    raise HTTPException(status_code=404, detail="Nenhum processo em andamento encontrado")


@router.get("/{identificador}")
def obter_processo(identificador: str):
    processo = ProcessoRepository.get_by_identifier(
        identificador,
        "codigo,project_token,nome_cliente,empresa,cpf,status,status_entrega,"
        "termo_dados,ressalvas_dados,nps_dados,imagens_termo,id,"
        "project_token_ativo,project_token_expira_em",
    )

    if not processo:
        raise HTTPException(status_code=404, detail="Processo nao encontrado")

    # Garante que os campos JSON sejam objetos Python, nao strings
    json_fields = ["termo_dados", "ressalvas_dados", "nps_dados", "imagens_termo"]
    for field in json_fields:
        value = processo.get(field)

        if value is None:
            processo[field] = {} if field.endswith("_dados") else []
            continue

        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                value = {} if field.endswith("_dados") else []

        if field.endswith("_dados") and not isinstance(value, dict):
            value = {}

        if field == "imagens_termo" and not isinstance(value, list):
            value = []

        processo[field] = value

    # Garante que o frontend sempre receba uma lista para 'imagens_termo'
    termo_dados = processo.get("termo_dados")
    if not isinstance(termo_dados, dict):
        termo_dados = {}
        processo["termo_dados"] = termo_dados

    if not processo.get("imagens_termo"):
        itens = termo_dados.get("itens")
        if isinstance(itens, list) and itens:
            processo["imagens_termo"] = itens

    return processo
