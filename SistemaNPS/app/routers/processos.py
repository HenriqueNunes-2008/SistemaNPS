import json

from fastapi import APIRouter, HTTPException

from app.services.processo_resolver import obter_processo_por_identificador
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
    processo = obter_processo_por_identificador(
        identificador,
        "codigo,project_token,nome_cliente,empresa,cpf,status,status_entrega,"
        "termo_dados,ressalvas_dados,nps_dados,imagens_termo",
    )

    # Garante que os campos JSON sejam objetos Python, nao strings
    json_fields = ["termo_dados", "ressalvas_dados", "nps_dados", "imagens_termo"]
    for field in json_fields:
        if isinstance(processo.get(field), str):
            try:
                processo[field] = json.loads(processo[field])
            except (json.JSONDecodeError, TypeError):
                if field.endswith("_dados"):
                    processo[field] = {}
                else:
                    processo[field] = []

    # Garante que o frontend sempre receba uma lista para 'imagens_termo'
    if "imagens_termo" not in processo or not processo["imagens_termo"]:
        if processo.get("termo_dados", {}).get("itens"):
            processo["imagens_termo"] = processo["termo_dados"]["itens"]

    return processo
