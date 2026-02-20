from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.final_pdf import regenerate_final_pdf_by_codigo
from app.services.supabase_client import supabase

router = APIRouter(prefix="/nps", tags=["NPS"])


class NPSRequest(BaseModel):
    processo_id: str
    nps: int
    avaliacoes: dict
    feedback: dict


class NPSUpdateRequest(BaseModel):
    processo_id: str
    nps: int
    avaliacoes: dict
    feedback: dict


@router.post("/finalizar")
def finalizar_nps(data: NPSRequest):
    try:
        processo_id = data.processo_id.strip()
        if not processo_id:
            raise HTTPException(status_code=400, detail="processo_id ausente")

        proc = (
            supabase
            .table("processos")
            .select("id")
            .eq("codigo", processo_id)
            .single()
            .execute()
        )
        if not proc.data:
            raise HTTPException(status_code=404, detail="Processo nao encontrado")

        processo_uuid = proc.data["id"]

        supabase.table("processos").update(
            {
                "nps_dados": {
                    "nps": data.nps,
                    "avaliacoes": data.avaliacoes,
                    "feedback": data.feedback,
                },
                "nps_nota": data.nps,
                "finalizado_em": date.today().isoformat(),
            }
        ).eq("id", processo_uuid).execute()

        final_url = regenerate_final_pdf_by_codigo(processo_id, set_status_finalizado=True)
        if not final_url:
            raise HTTPException(status_code=400, detail="Nao foi possivel gerar PDF final sem dados completos")

        return {"status": "ok", "pdf_final": final_url}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@router.post("/atualizar")
def atualizar_nps(data: NPSUpdateRequest):
    try:
        processo_id = data.processo_id.strip()
        if not processo_id:
            raise HTTPException(status_code=400, detail="processo_id ausente")

        proc = (
            supabase
            .table("processos")
            .select("id")
            .eq("codigo", processo_id)
            .single()
            .execute()
        )

        if not proc.data:
            raise HTTPException(status_code=404, detail="Processo nao encontrado")

        processo_uuid = proc.data["id"]

        supabase.table("processos").update(
            {
                "nps_dados": {
                    "nps": data.nps,
                    "avaliacoes": data.avaliacoes,
                    "feedback": data.feedback,
                },
                "nps_nota": data.nps,
                "atualizado_em": date.today().isoformat(),
            }
        ).eq("id", processo_uuid).execute()

        final_url = regenerate_final_pdf_by_codigo(processo_id, set_status_finalizado=False)
        return {"status": "ok", "pdf_final": final_url}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")