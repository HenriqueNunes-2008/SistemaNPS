from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from app.services.processo_repository import ProcessoRepository
from app.services.processo_service import ProcessoService
from app.services.shared_data import dados_compartilhados, as_dict
from app.services.termos import salvar_termo_extra
from app.routers.security import is_admin_mode_request

router = APIRouter(prefix="/termos", tags=["Termos"])


class TermoExtraRequest(BaseModel):
    processo_id: str
    dados: dict = Field(default_factory=dict)


class AvancarFluxoRequest(BaseModel):
    processo_id: str
    etapa: str


def _get(identifier: str):
    proc = ProcessoRepository.get_by_identifier(identifier)
    if not proc:
        raise HTTPException(404, "Processo não encontrado")
    return proc


def _documentos_prontos(proc: dict) -> bool:
    return not ProcessoService.documentos_obrigatorios_pendentes(proc)


@router.get("/{tipo}/dados/{identificador}")
def obter_dados(
    tipo: str,
    identificador: str,
    response: Response,
    request: Request,
):
    if tipo not in ("recebimento", "treinamento"):
        raise HTTPException(404, "Termo não encontrado")
    response.headers["Cache-Control"] = "no-store"
    proc = _get(identificador)
    if (
        not is_admin_mode_request(request)
        and ProcessoService.is_token_expired(proc)
    ):
        raise HTTPException(403, "Token expirado.")
    return {
        "success": True,
        "bloqueado": bool(
            as_dict(proc.get("nps_dados")).get("_lock_termo")
            or as_dict(proc.get("nps_dados")).get("_lock_ressalvas")
            or as_dict(proc.get("nps_dados")).get("_lock_nps")
            or as_dict(proc.get("nps_dados")).get("_edicao_bloqueada")
        ),
        "assinatura_concluida": bool(
            proc.get("assinatura_cliente_url")
            or as_dict(proc.get("termo_dados")).get("assinatura_cliente_path")
        ),
        "documentos_prontos": _documentos_prontos(proc),
        "documentos_pendentes": ProcessoService.documentos_obrigatorios_pendentes(proc),
        "etapa_atual": ProcessoService.etapa_atual_cliente(proc),
        "dados": as_dict(proc.get(f"{tipo}_dados")) | dados_compartilhados(proc),
    }


@router.post("/avancar")
def avancar_fluxo(data: AvancarFluxoRequest, request: Request):
    if is_admin_mode_request(request):
        raise HTTPException(403, "O avanço do fluxo é exclusivo do cliente.")
    proc = _get(data.processo_id)
    if ProcessoService.is_token_expired(proc) or str(proc.get("status", "")).lower() == "finalizado":
        raise HTTPException(403, "Token expirado ou processo finalizado.")
    if ProcessoService.etapa_atual_cliente(proc) != data.etapa:
        raise HTTPException(409, "Esta etapa não está liberada para avanço.")
    try:
        proxima = ProcessoService.proxima_etapa_cliente(proc, data.etapa)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"success": True, "proxima_etapa": proxima}


@router.get("/recebimento/dados/{identificador}", include_in_schema=False)
def obter_recebimento(
    identificador: str,
    response: Response,
    request: Request,
):
    return obter_dados("recebimento", identificador, response, request)


@router.get("/treinamento/dados/{identificador}", include_in_schema=False)
def obter_treinamento(
    identificador: str,
    response: Response,
    request: Request,
):
    return obter_dados("treinamento", identificador, response, request)


@router.post("/{tipo}/salvar")
def salvar(tipo: str, data: TermoExtraRequest, request: Request):
    if tipo not in ("recebimento", "treinamento"):
        raise HTTPException(404, "Termo não encontrado")
    if not is_admin_mode_request(request):
        raise HTTPException(403, "Somente o administrador pode preencher este termo.")
    proc = _get(data.processo_id)
    if str(proc.get("status", "")).lower() == "finalizado":
        raise HTTPException(403, "Processo bloqueado ou finalizado.")
    nps_dados = as_dict(proc.get("nps_dados"))
    if (
        nps_dados.get("_edicao_bloqueada")
        or nps_dados.get("_lock_termo")
        or nps_dados.get("_lock_ressalvas")
    ):
        raise HTTPException(403, "Edição dos documentos bloqueada.")
    return {"success": True, **salvar_termo_extra(proc, tipo, data.dados)}


@router.post("/recebimento/salvar", include_in_schema=False)
def salvar_recebimento(data: TermoExtraRequest, request: Request):
    return salvar("recebimento", data, request)


@router.post("/treinamento/salvar", include_in_schema=False)
def salvar_treinamento(data: TermoExtraRequest, request: Request):
    return salvar("treinamento", data, request)
