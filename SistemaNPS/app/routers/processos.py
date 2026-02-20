import json

from fastapi import APIRouter, HTTPException

from app.services.supabase_client import supabase

router = APIRouter(prefix="/api/processos", tags=["Processos"])


@router.get("/ultimo-em-andamento")
def obter_ultimo_processo_em_andamento():
    res = (
        supabase
        .table("processos")
        .select("codigo,status,atualizado_em,criado_em")
        .order("atualizado_em", desc=True)
        .order("criado_em", desc=True)
        .limit(30)
        .execute()
    )

    processos = res.data or []
    for processo in processos:
        status = str(processo.get("status") or "").strip().lower()
        if status != "finalizado" and processo.get("codigo"):
            return {"processo_id": processo["codigo"]}

    raise HTTPException(status_code=404, detail="Nenhum processo em andamento encontrado")


@router.get("/{codigo}")
def obter_processo(codigo: str):
    res = (
        supabase
        .table("processos")
        .select(
            "codigo,nome_cliente,empresa,cpf,status_entrega,"
            "termo_dados,ressalvas_dados,nps_dados,imagens_termo"
        )
        .eq("codigo", codigo)
        .single()
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=404, detail="Processo nao encontrado")

    # Garante que os campos JSON sejam objetos Python, não strings
    json_fields = ["termo_dados", "ressalvas_dados", "nps_dados", "imagens_termo"]
    for field in json_fields:
        if isinstance(res.data.get(field), str):
            try:
                res.data[field] = json.loads(res.data[field])
            except (json.JSONDecodeError, TypeError):
                # Se falhar, define um valor padrão seguro
                if field.endswith("_dados"):
                    res.data[field] = {}
                else:
                    res.data[field] = []

    # Garante que o frontend sempre receba uma lista para 'imagens_termo'
    if "imagens_termo" not in res.data or not res.data["imagens_termo"]:
        if res.data.get("termo_dados", {}).get("itens"):
            res.data["imagens_termo"] = res.data["termo_dados"]["itens"]

    return res.data
