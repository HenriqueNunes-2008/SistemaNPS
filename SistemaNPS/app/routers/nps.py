from datetime import date

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.services.processo_repository import ProcessoRepository
from app.services.processo_service import ProcessoService
from app.routers.utils import parse_json_object
from app.routers.security import is_admin_activation_granted

router = APIRouter(prefix="/nps", tags=["NPS"])

def _is_user_edit_locked(proc: dict | None) -> bool:
    nps_dados = parse_json_object((proc or {}).get("nps_dados"))
    return bool(nps_dados.get("_lock_nps"))


def _extract_user_flow(request: Request) -> str:
    flow = (request.cookies.get("nps_tipo_acesso") or "").strip().lower()
    return flow if flow in ("cliente", "motorista") else "cliente"


class NPSRequest(BaseModel):
    processo_id: str
    nps: int = Field(..., ge=0, le=10) # Garante que NPS seja entre 0 e 10
    avaliacoes: dict = Field(default_factory=dict)
    feedback: dict = Field(default_factory=dict)


class NPSUpdateRequest(BaseModel):
    processo_id: str
    nps: int
    avaliacoes: dict
    feedback: dict


@router.post("/finalizar")
def finalizar_nps(data: NPSRequest, request: Request):
    try:
        if is_admin_activation_granted(request):
            raise HTTPException(status_code=403, detail="Administradores não podem preencher o NPS.")
        # Removido check admin/motorista para acesso direto
        processo_id = data.processo_id.strip()
        if not processo_id:
            raise HTTPException(status_code=400, detail="processo_id ausente")

        proc = ProcessoRepository.get_by_identifier(processo_id)
        if not proc:
            raise HTTPException(status_code=404, detail="Processo não encontrado")

        if ProcessoService.is_token_expired(proc):
            raise HTTPException(status_code=403, detail="Token expirado: processo bloqueado para edicao")
        if _is_user_edit_locked(proc):
            raise HTTPException(status_code=403, detail="Edicao bloqueada para este processo. Solicite liberacao ao admin")
        processo_uuid = proc["id"]
        processo_codigo = proc.get("codigo") or processo_id

        final_url = ProcessoService.finalizar_nps_fluxo(
            processo_uuid, data.nps, data.dict(), _extract_user_flow(request), processo_codigo, is_update=False
        )

        return {"status": "ok", "pdf_final": final_url}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/atualizar")
def atualizar_nps(data: NPSUpdateRequest, request: Request):
    try:
        if is_admin_activation_granted(request):
            raise HTTPException(status_code=403, detail="Administradores não podem preencher o NPS.")
        # Removido check admin/motorista para acesso direto
        processo_id = data.processo_id.strip()
        if not processo_id:
            raise HTTPException(status_code=400, detail="processo_id ausente")

        proc = ProcessoRepository.get_by_identifier(processo_id)
        if not proc:
            raise HTTPException(status_code=404, detail="Processo não encontrado")

        if ProcessoService.is_token_expired(proc):
            raise HTTPException(status_code=403, detail="Token expirado: processo bloqueado para edicao")
        if _is_user_edit_locked(proc):
            raise HTTPException(status_code=403, detail="Edicao bloqueada para este processo. Solicite liberacao ao admin")
        processo_uuid = proc["id"]
        processo_codigo = proc.get("codigo") or processo_id

        final_url = ProcessoService.finalizar_nps_fluxo(
            processo_uuid, data.nps, data.dict(), _extract_user_flow(request), processo_codigo, is_update=True
        )
        return {"status": "ok", "pdf_final": final_url}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
