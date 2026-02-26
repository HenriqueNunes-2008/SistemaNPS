from datetime import date
import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services.final_pdf import regenerate_final_pdf_by_codigo
from app.services.processo_resolver import obter_processo_por_identificador
from app.services.supabase_client import supabase

router = APIRouter(prefix="/nps", tags=["NPS"])


def _is_admin_request(request: Request) -> bool:
    role_cookie = (request.cookies.get("nps_role") or "").strip().lower()
    return role_cookie == "admin"


def _processo_token_finalizado(proc: dict | None) -> bool:
    if not isinstance(proc, dict):
        return False
    if proc.get("project_token_ativo") is False:
        return True
    return bool(proc.get("project_token_expira_em"))


def _parse_json_object(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _is_user_edit_locked(proc: dict | None) -> bool:
    nps_dados = _parse_json_object((proc or {}).get("nps_dados"))
    return bool(nps_dados.get("_lock_nps"))


def _extract_user_flow(request: Request) -> str:
    flow = (request.cookies.get("nps_tipo_acesso") or "").strip().lower()
    return flow if flow in ("cliente", "motorista") else "cliente"


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
def finalizar_nps(data: NPSRequest, request: Request):
    try:
        if _is_admin_request(request):
            raise HTTPException(status_code=403, detail="Admin apenas visualiza a pesquisa NPS")
        if _extract_user_flow(request) == "motorista":
            raise HTTPException(status_code=403, detail="Motorista nao possui permissao para responder NPS")
        processo_id = data.processo_id.strip()
        if not processo_id:
            raise HTTPException(status_code=400, detail="processo_id ausente")

        proc = obter_processo_por_identificador(
            processo_id,
            "id,codigo,project_token,project_token_ativo,project_token_expira_em,nps_dados",
        )
        if _processo_token_finalizado(proc):
            raise HTTPException(status_code=403, detail="Token expirado: processo bloqueado para edicao")
        if _is_user_edit_locked(proc):
            raise HTTPException(status_code=403, detail="Edicao bloqueada para este processo. Solicite liberacao ao admin")
        processo_uuid = proc["id"]
        processo_codigo = proc.get("codigo") or processo_id

        nps_dados = _parse_json_object(proc.get("nps_dados"))
        nps_dados.update({
            "nps": data.nps,
            "avaliacoes": data.avaliacoes,
            "feedback": data.feedback,
        })
        nps_dados["_lock_nps"] = True
        nps_dados["_lock_nps_por"] = _extract_user_flow(request)

        supabase.table("processos").update(
            {
                "nps_dados": nps_dados,
                "nps_nota": data.nps,
                "finalizado_em": date.today().isoformat(),
            }
        ).eq("id", processo_uuid).execute()

        final_url = regenerate_final_pdf_by_codigo(processo_codigo, set_status_finalizado=True)
        if not final_url:
            raise HTTPException(status_code=400, detail="Nao foi possivel gerar PDF final sem dados completos")

        return {"status": "ok", "pdf_final": final_url}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@router.post("/atualizar")
def atualizar_nps(data: NPSUpdateRequest, request: Request):
    try:
        if _is_admin_request(request):
            raise HTTPException(status_code=403, detail="Admin apenas visualiza a pesquisa NPS")
        if _extract_user_flow(request) == "motorista":
            raise HTTPException(status_code=403, detail="Motorista nao possui permissao para responder NPS")
        processo_id = data.processo_id.strip()
        if not processo_id:
            raise HTTPException(status_code=400, detail="processo_id ausente")

        proc = obter_processo_por_identificador(
            processo_id,
            "id,codigo,project_token,project_token_ativo,project_token_expira_em,nps_dados",
        )
        if _processo_token_finalizado(proc):
            raise HTTPException(status_code=403, detail="Token expirado: processo bloqueado para edicao")
        if _is_user_edit_locked(proc):
            raise HTTPException(status_code=403, detail="Edicao bloqueada para este processo. Solicite liberacao ao admin")
        processo_uuid = proc["id"]
        processo_codigo = proc.get("codigo") or processo_id

        nps_dados = _parse_json_object(proc.get("nps_dados"))
        nps_dados.update({
            "nps": data.nps,
            "avaliacoes": data.avaliacoes,
            "feedback": data.feedback,
        })
        nps_dados["_lock_nps"] = True
        nps_dados["_lock_nps_por"] = _extract_user_flow(request)

        supabase.table("processos").update(
            {
                "nps_dados": nps_dados,
                "nps_nota": data.nps,
                "atualizado_em": date.today().isoformat(),
            }
        ).eq("id", processo_uuid).execute()

        final_url = regenerate_final_pdf_by_codigo(processo_codigo, set_status_finalizado=False)
        return {"status": "ok", "pdf_final": final_url}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")
