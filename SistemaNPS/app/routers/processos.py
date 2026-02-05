from fastapi import APIRouter, HTTPException

from app.services.supabase_client import supabase

router = APIRouter(prefix="/api/processos", tags=["Processos"])


@router.get("/{codigo}")
def obter_processo(codigo: str):
    res = (
        supabase
        .table("processos")
        .select(
            "codigo,nome_cliente,empresa,cpf,status_entrega,"
            "termo_dados,ressalvas_dados,nps_dados"
        )
        .eq("codigo", codigo)
        .single()
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=404, detail="Processo não encontrado")

    return res.data
