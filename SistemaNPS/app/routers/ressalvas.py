import os
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, date
import base64
from io import BytesIO
from app.services.processo_repository import ProcessoRepository
from app.services.processo_service import ProcessoService
from app.services.pdf_service import gerar_pdf_ressalvas_buffer
from app.routers.utils import normalize_base64, gerar_hash_imagem, parse_json_object
from app.routers.security import is_admin_mode_request

router = APIRouter(prefix="/ressalvas", tags=["Ressalvas"])

def _is_user_edit_locked(proc: dict | None) -> bool:
    nps_dados = parse_json_object((proc or {}).get("nps_dados"))
    return bool(nps_dados.get("_lock_ressalvas"))


def _extract_user_flow(request: Request) -> str:
    flow = (request.cookies.get("nps_tipo_acesso") or "").strip().lower()
    return flow if flow in ("cliente", "motorista") else "cliente"

# ============================================================
# MODELS
# ============================================================

class ImagemRessalva(BaseModel):
    item: str
    descricao: str
    prazo: Optional[date] = None
    responsavel: Optional[str] = None
    observacao: Optional[str] = None
    aprovacao: bool = False
    imagem_base64: Optional[str] = None


class RessalvasRequest(BaseModel):
    processo_id: str  # project_token (principal) ou codigo (compatibilidade)
    responsavel: str
    cpf: Optional[str] = None
    observacoes: Optional[str] = None
    imagens: List[ImagemRessalva]


class RessalvasUpdateRequest(BaseModel):
    processo_id: str
    responsavel: str
    cpf: Optional[str] = None
    observacoes: Optional[str] = None
    imagens: List[ImagemRessalva]


class RessalvasResponse(BaseModel):
    success: bool
    pdf_url: Optional[str] = None

# ============================================================
# ROUTE
# ============================================================

@router.post("/salvar", response_model=RessalvasResponse)
def salvar_ressalvas(data: RessalvasRequest, request: Request):
    try:
        proc = ProcessoRepository.get_by_identifier(data.processo_id)
        if not proc:
            raise HTTPException(status_code=404, detail="Processo não encontrado")
        
        if ProcessoService.is_token_expired(proc):
            raise HTTPException(status_code=403, detail="Token expirado: processo bloqueado para edição")
        if _is_user_edit_locked(proc):
            raise HTTPException(status_code=403, detail="Edição bloqueada para este processo. Solicite liberação ao admin")

        pdf_url, _ = ProcessoService.salvar_ressalvas_fluxo(data, is_update=False, existing_proc=proc)

        return RessalvasResponse(success=True, pdf_url=pdf_url)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/atualizar", response_model=RessalvasResponse)
def atualizar_ressalvas(data: RessalvasUpdateRequest, request: Request):
    try:
        proc = ProcessoRepository.get_by_identifier(data.processo_id)
        if not proc:
            raise HTTPException(status_code=404, detail="Processo não encontrado")
        
        if ProcessoService.is_token_expired(proc):
            raise HTTPException(status_code=403, detail="Token expirado: processo bloqueado para edição")

        # Bypass de bloqueio para Admin
        is_admin = is_admin_mode_request(request)
        if _is_user_edit_locked(proc) and not is_admin:
            raise HTTPException(status_code=403, detail="Edição bloqueada.")

        pdf_url, _ = ProcessoService.salvar_ressalvas_fluxo(data, is_update=True, existing_proc=proc)

        return RessalvasResponse(success=True, pdf_url=pdf_url)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/dados/{identificador}")
def obter_dados_ressalvas(identificador: str, response: Response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    try:
        proc = ProcessoRepository.get_by_identifier(identificador)
        if not proc:
            raise HTTPException(status_code=404, detail="Processo não encontrado")
        
        ressalvas_dados = parse_json_object(proc.get("ressalvas_dados"))
        nps_dados = parse_json_object(proc.get("nps_dados"))
        
        # Se nao houver dados salvos no JSON, tenta buscar itens da tabela ressalvas_itens
        itens = ressalvas_dados.get("itens")
        if not itens:
            itens_db = ProcessoRepository.get_ressalvas_itens(proc["id"])
            # Mapeia estrutura DB -> Frontend
            itens = []
            for row in itens_db:
                itens.append({
                    "item": row.get("item"),
                    "descricao": row.get("descricao"),
                    "prazo": row.get("prazo"),
                    "aprovacao": row.get("aprovacao"),
                    "imagem_hash": row.get("imagem_hash")
                })

        dados = {
            "codigo": proc.get("codigo"),
            "project_token": proc.get("project_token"),
            "project_token_ativo": proc.get("project_token_ativo"),
            "project_token_expira_em": proc.get("project_token_expira_em"),
            "ressalvas_dados": ressalvas_dados,
            "nps_dados": nps_dados,
            "bloqueado": bool(nps_dados.get("_lock_ressalvas"))
        }

        # Espalha campos adicionais do JSON na raiz (Flattening)
        if isinstance(ressalvas_dados, dict):
            for k, v in ressalvas_dados.items():
                if k not in dados and k not in ("itens",):
                    dados[k] = v
            
            # Se houver campos extras dentro de uma chave 'campos' (padrão similar ao termo), extrai também
            campos_internos = ressalvas_dados.get("campos")
            if isinstance(campos_internos, dict):
                for k, v in campos_internos.items():
                    if k not in dados:
                        dados[k] = v

        return {"success": True, "dados": dados}

    except Exception:
        raise HTTPException(status_code=404, detail="Processo nao encontrado")
