import re
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel
from typing import List, Optional

from app.services.processo_repository import ProcessoRepository
from app.services.processo_service import ProcessoService
from app.routers.utils import parse_json_object, parse_json_list
from app.routers.security import is_admin_mode_request

router = APIRouter(prefix="/termo", tags=["Termo"])

def _is_user_edit_locked(proc: dict | None) -> bool:
    nps_dados = parse_json_object((proc or {}).get("nps_dados"))
    return bool(nps_dados.get("_lock_termo"))

def _extract_user_flow(request: Request) -> str:
    flow = (request.cookies.get("nps_tipo_acesso") or "").strip().lower()
    return flow if flow in ("cliente", "motorista") else "cliente"

# ============================================================
# MODEL
# ============================================================

class ImagemTermo(BaseModel):
    item: str | int
    regiao_foto: str | None = None
    imagem_base64: str | None = None
    imagem_hash: str | None = None

class TermoRequest(BaseModel):
    processo_id: Optional[str] = None
    cpf: str
    nome_cliente: str
    empresa: str | None = None
    status_entrega: str
    imagem: str  # base64 (data:image/...)
    imagens: List[ImagemTermo] | list = []
    termo_dados: dict | None = None
    campos: dict | None = None
    assinaturas: dict | None = None
    data: dict | None = None

class TermoUpdateRequest(BaseModel):
    processo_codigo: str
    cpf: str
    nome_cliente: str
    empresa: str | None = None
    status_entrega: str
    imagem: str
    imagens: List[ImagemTermo] | list = []
    termo_dados: dict | None = None
    campos: dict | None = None
    assinaturas: dict | None = None
    data: dict | None = None

# ============================================================
# ROTA
# ============================================================

@router.post("/salvar")
def salvar_termo(data: TermoRequest):
    try:
        existing_proc = ProcessoRepository.get_by_identifier(data.processo_id) if data.processo_id else None

        # Validações rápidas
        cpf_limpo = re.sub(r"\D", "", data.cpf)
        if not re.fullmatch(r"\d{11}", cpf_limpo):
            raise HTTPException(status_code=400, detail="CPF inválido")
        if not data.nome_cliente.strip():
            raise HTTPException(status_code=400, detail="Nome do cliente obrigatório")
        
        # Chamar o servico que faz o trabalho pesado
        result = ProcessoService.salvar_termo_fluxo(data, is_update=False, existing_proc=existing_proc)

        return {
            "success": True,
            "processo_id": result["project_token"],
            "codigo": result["codigo"],
            "project_token": result["project_token"]
        }
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/atualizar")
def atualizar_termo(data: TermoUpdateRequest, request: Request):
    try:
        proc = ProcessoRepository.get_by_identifier(data.processo_codigo)
        if not proc:
            raise HTTPException(status_code=404, detail="Processo não encontrado")

        # Validação de bloqueio ignorada para Admin
        is_admin = is_admin_mode_request(request)
        if _is_user_edit_locked(proc) and not is_admin:
            # Permitimos a atualização pelo cliente se ele estiver preenchendo a aprovação pela primeira vez
            dados_atuais = parse_json_object(proc.get("termo_dados"))
            aprov_atual = dados_atuais.get("aprovacao") or {}
            
            # Se já existir representante e cpf preenchidos na aprovação, bloqueamos de fato para evitar re-edição
            if aprov_atual.get("representante") and aprov_atual.get("cpf"):
                raise HTTPException(status_code=403, detail="Edição bloqueada: Termo já aprovado.")
            
            # Se o campo de aprovação no payload enviado está vazio, bloqueamos (tentativa de editar outros campos)
            if not data.termo_dados or not data.termo_dados.get("aprovacao", {}).get("representante"):
                raise HTTPException(status_code=403, detail="Edição bloqueada para este processo.")

        # Se for admin, forçamos o modo visualização para a Aprovação Final:
        # Mantemos os dados de aprovação originais do banco, ignorando o que veio no payload.
        if is_admin and data.termo_dados:
            dados_originais = parse_json_object(proc.get("termo_dados"))
            data.termo_dados["aprovacao"] = dados_originais.get("aprovacao")

        result = ProcessoService.salvar_termo_fluxo(data, is_update=True, existing_proc=proc)

        return {
            "success": True,
            "processo_id": result["project_token"],
            "codigo": result["codigo"],
            "project_token": result["project_token"]
        }
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))
    
@router.get("/dados/{identificador}")
def obter_dados_termo(identificador: str, response: Response, request: Request):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    try:
        proc = ProcessoRepository.get_by_identifier(identificador)
        if not proc:
            raise HTTPException(status_code=404, detail="Processo não encontrado")
        
        # Parse dos dados JSON para garantir que cheguem como objeto ao frontend
        termo_dados = parse_json_object(proc.get("termo_dados"))
        nps_dados = parse_json_object(proc.get("nps_dados"))
        
        dados = {
            "codigo": proc.get("codigo"),
            "project_token": proc.get("project_token"),
            "nome_cliente": proc.get("nome_cliente"),
            "empresa": proc.get("empresa"),
            "cpf": proc.get("cpf"),
            "status_entrega": proc.get("status_entrega"),
            "termo_dados": termo_dados,
            "nps_dados": nps_dados,
            "imagens": proc.get("imagens_termo") or [],
            "bloqueado": bool(nps_dados.get("_lock_termo")),
            "is_admin": is_admin_mode_request(request)
        }

        # Expõe campos internos do termo_dados na raiz para facilitar o preenchimento no frontend
        if isinstance(termo_dados, dict):
            for k, v in termo_dados.items():
                # Ignora chaves complexas que já tratamos ou não são campos diretos
                if k not in dados and k not in ("campos", "itens"):
                    dados[k] = v
            
            # DEEP FLATTENING: Extrai os campos dinâmicos (ex: inputs do formulário) para a raiz
            # Isso garante que inputs com name="endereco" recebam o valor corretamente
            campos_internos = termo_dados.get("campos")
            if isinstance(campos_internos, dict):
                for k, v in campos_internos.items():
                    if k not in dados:
                        dados[k] = v

        return {
            "success": True,
            "dados": dados
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail="Processo não encontrado")
