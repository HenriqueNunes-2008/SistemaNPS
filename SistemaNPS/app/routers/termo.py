import re
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from typing import List, Optional

from app.services.processo_repository import ProcessoRepository
from app.services.processo_service import ProcessoService
from app.routers.utils import parse_json_object, parse_json_list
from app.routers.security import is_admin_mode_request
from app.services.assinatura_service import salvar_assinatura
from app.services.termos import salvar_termo_extra


router = APIRouter(prefix="/termo", tags=["Termo"])


# ============================================================
# HELPERS
# ============================================================

def _is_user_edit_locked(proc: dict | None) -> bool:
    nps_dados = parse_json_object((proc or {}).get("nps_dados"))
    return bool(nps_dados.get("_lock_termo"))


def _extract_user_flow(request: Request) -> str:
    flow = (request.cookies.get("nps_tipo_acesso") or "").strip().lower()
    return flow if flow in ("cliente", "motorista") else "cliente"


# ============================================================
# MODELS
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
    imagem: str
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


class AssinaturaRequest(BaseModel):
    processo_codigo: str
    assinatura: str


# ============================================================
# SALVAR TERMO
# ============================================================

@router.post("/salvar")
def salvar_termo(data: TermoRequest, request: Request):
    try:
        if not is_admin_mode_request(request):
            raise HTTPException(
                status_code=403,
                detail="Somente o administrador pode preencher o termo."
            )

        if data.termo_dados:
            data.termo_dados["aprovacao"] = None

        existing_proc = (
            ProcessoRepository.get_by_identifier(data.processo_id)
            if data.processo_id
            else None
        )

        # ----------------------------------------------------
        # Validações
        # ----------------------------------------------------

        cpf_limpo = re.sub(r"\D", "", data.cpf)

        if not re.fullmatch(r"\d{11}", cpf_limpo):
            raise HTTPException(
                status_code=400,
                detail="CPF inválido"
            )

        if not data.nome_cliente.strip():
            raise HTTPException(
                status_code=400,
                detail="Nome do cliente obrigatório"
            )

        # ----------------------------------------------------
        # Serviço principal
        # ----------------------------------------------------

        result = ProcessoService.salvar_termo_fluxo(
            data,
            is_update=False,
            existing_proc=existing_proc
        )

        return {
            "success": True,
            "processo_id": result["project_token"],
            "codigo": result["codigo"],
            "project_token": result["project_token"]
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# ============================================================
# ATUALIZAR TERMO
# ============================================================

@router.post("/atualizar")
def atualizar_termo(
    data: TermoUpdateRequest,
    request: Request
):
    try:
        proc = ProcessoRepository.get_by_identifier(
            data.processo_codigo
        )

        if not proc:
            raise HTTPException(
                status_code=404,
                detail="Processo não encontrado"
            )

        if ProcessoService.is_token_expired(proc) or str(proc.get("status", "")).lower() == "finalizado":
            raise HTTPException(status_code=403, detail="Token expirado ou processo finalizado.")

        is_admin = is_admin_mode_request(request)

        if _is_user_edit_locked(proc) and not is_admin:
            raise HTTPException(
                status_code=403,
                detail="Termo bloqueado. Use a etapa de assinatura digital."
            )

        # ----------------------------------------------------
        # ADMIN
        #
        # Mantém os dados originais e permite somente alterar
        # a assinatura do representante.
        # ----------------------------------------------------

        if is_admin and data.termo_dados:

            dados_originais = parse_json_object(
                proc.get("termo_dados")
            )

            if dados_originais:

                assinaturas_originais = (
                    dados_originais.get("assinaturas")
                    or {}
                )

                assinaturas_recebidas = (
                    data.termo_dados.get("assinaturas")
                    or {}
                )

                representante = (
                    assinaturas_recebidas.get("representante")
                    or {}
                )

                data.termo_dados = dict(
                    dados_originais
                )

                data.termo_dados["assinaturas"] = dict(
                    assinaturas_originais
                )

                data.termo_dados["assinaturas"][
                    "representante"
                ] = representante

                data.cpf = (
                    proc.get("cpf")
                    or data.cpf
                )

                data.nome_cliente = (
                    proc.get("nome_cliente")
                    or data.nome_cliente
                )

                data.empresa = proc.get("empresa")

                data.status_entrega = (
                    proc.get("status_entrega")
                    or data.status_entrega
                )

        # ----------------------------------------------------
        # Serviço
        # ----------------------------------------------------

        result = ProcessoService.salvar_termo_fluxo(
            data,
            is_update=True,
            existing_proc=proc
        )

        return {
            "success": True,
            "processo_id": result["project_token"],
            "codigo": result["codigo"],
            "project_token": result["project_token"]
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# ============================================================
# OBTER DADOS DO TERMO
# ============================================================

@router.get("/dados/{identificador}")
def obter_dados_termo(
    identificador: str,
    response: Response,
    request: Request
):
    response.headers["Cache-Control"] = (
        "no-cache, no-store, must-revalidate"
    )
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    try:
        proc = ProcessoRepository.get_by_identifier(
            identificador
        )

        if not proc:
            raise HTTPException(
                status_code=404,
                detail="Processo não encontrado"
            )

        # ----------------------------------------------------
        # Parse JSON
        # ----------------------------------------------------

        termo_dados = parse_json_object(
            proc.get("termo_dados")
        )

        nps_dados = parse_json_object(
            proc.get("nps_dados")
        )

        # ----------------------------------------------------
        # Dados principais
        # ----------------------------------------------------

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

            "bloqueado": bool(
                nps_dados.get("_lock_termo")
            ),

            "is_admin": is_admin_mode_request(request),

            # ------------------------------------------------
            # NOVO:
            # informa explicitamente se o cliente já assinou.
            # ------------------------------------------------
            "assinatura_cliente": bool(
                termo_dados.get(
                    "assinatura_cliente_path"
                )
            )
        }

        # ----------------------------------------------------
        # Expõe campos internos na raiz
        # ----------------------------------------------------

        if isinstance(termo_dados, dict):

            for k, v in termo_dados.items():

                if k not in dados and k not in (
                    "campos",
                    "itens"
                ):
                    dados[k] = v

            # ------------------------------------------------
            # Deep flattening dos campos dinâmicos
            # ------------------------------------------------

            campos_internos = termo_dados.get(
                "campos"
            )

            if isinstance(campos_internos, dict):

                for k, v in campos_internos.items():

                    if k not in dados:
                        dados[k] = v

        return {
            "success": True,
            "dados": dados
        }

    except HTTPException:
        raise

    except Exception:
        raise HTTPException(
            status_code=404,
            detail="Processo não encontrado"
        )


# ============================================================
# ASSINATURA DIGITAL DO CLIENTE
# ============================================================

@router.post("/assinatura")
def salvar_assinatura_cliente(
    data: AssinaturaRequest,
    request: Request
):
    """
    Salva a assinatura digital do cliente.

    Fluxo:

        Canvas
          ↓
        Base64
          ↓
        FastAPI
          ↓
        Supabase Storage
          ↓
        caminho do PNG
          ↓
        termo_dados.assinatura_cliente_path

    A assinatura é feita uma única vez e posteriormente
    reutilizada nos quatro documentos.
    """

    try:

        # ----------------------------------------------------
        # Somente cliente pode assinar.
        # ----------------------------------------------------

        if is_admin_mode_request(request):
            raise HTTPException(
                status_code=403,
                detail=(
                    "A assinatura digital deve ser "
                    "realizada pelo cliente."
                )
            )

        # ----------------------------------------------------
        # Localiza processo.
        # ----------------------------------------------------

        proc = ProcessoRepository.get_by_identifier(
            data.processo_codigo
        )

        if not proc:
            raise HTTPException(
                status_code=404,
                detail="Processo não encontrado."
            )

        if ProcessoService.is_token_expired(proc) or str(proc.get("status", "")).lower() == "finalizado":
            raise HTTPException(status_code=403, detail="Token expirado ou processo finalizado.")

        # ----------------------------------------------------
        # O bloqueio dos termos anteriores não impede a assinatura única.
        # ----------------------------------------------------

        etapa_atual = ProcessoService.etapa_atual_cliente(proc)
        if etapa_atual != "assinatura":
            raise HTTPException(status_code=409, detail="A assinatura ainda não está liberada.")
        documentos_pendentes = ProcessoService.documentos_obrigatorios_pendentes(proc)
        if documentos_pendentes:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Não é possível assinar: "
                    + ", ".join(documentos_pendentes)
                    + (
                        " ainda não foi concluído."
                        if len(documentos_pendentes) == 1
                        else " ainda não foram concluídos."
                    )
                ),
            )

        # ----------------------------------------------------
        # Recupera dados atuais.
        # ----------------------------------------------------

        termo_dados = parse_json_object(
            proc.get("termo_dados")
        )

        # ----------------------------------------------------
        # Não permite substituir assinatura existente.
        # ----------------------------------------------------

        assinatura_existente = proc.get("assinatura_cliente_url") or termo_dados.get("assinatura_cliente_url") or termo_dados.get("assinatura_cliente_path")

        if assinatura_existente:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Este processo já possui "
                    "uma assinatura digital."
                )
            )

        # ----------------------------------------------------
        # Validação básica.
        # ----------------------------------------------------

        if not data.assinatura:
            raise HTTPException(
                status_code=400,
                detail="Assinatura não informada."
            )

        # ----------------------------------------------------
        # Salva PNG no Supabase Storage.
        # ----------------------------------------------------

        caminho = salvar_assinatura(
            assinatura_base64=data.assinatura,
            processo_id=str(proc["id"])
        )

        # ----------------------------------------------------
        # Salva somente o caminho no banco.
        # ----------------------------------------------------

        result = ProcessoService.salvar_assinatura_cliente_fluxo(proc, caminho)
        termo_dados = parse_json_object(result.get("termo_dados"))

        # Atualiza também o PDF do aceite com a assinatura recém-salva.
        ProcessoService.salvar_termo_fluxo(
            SimpleNamespace(
                cpf=result.get("cpf") or "",
                nome_cliente=result.get("nome_cliente") or "",
                empresa=result.get("empresa"),
                status_entrega=result.get("status_entrega") or "",
                termo_dados=termo_dados,
                imagens=[],
                campos=None,
                assinaturas=None,
                data=None,
            ),
            is_update=True,
            existing_proc=result,
        )

        # Regenera os termos extras para que a mesma assinatura apareça neles.
        ressalvas_dados = parse_json_object(result.get("ressalvas_dados"))
        ressalvas = parse_json_list(ressalvas_dados.get("itens"))
        if ressalvas:
            ProcessoService.salvar_ressalvas_fluxo(
                SimpleNamespace(
                    processo_id=result.get("codigo") or data.processo_codigo,
                    responsavel=ressalvas_dados.get("responsavel") or "",
                    cpf=ressalvas_dados.get("cpf"),
                    observacoes=ressalvas_dados.get("observacoes"),
                    imagens=[SimpleNamespace(**item) for item in ressalvas if isinstance(item, dict)],
                ),
                is_update=True,
                existing_proc=result,
            )

        for tipo in ("recebimento", "treinamento"):
            salvar_termo_extra(result, tipo)

        return {
            "success": True,
            "assinatura_salva": True,
            "assinatura_cliente_url": caminho,
            "processo_id": result["id"]
        }

    except HTTPException:
        raise

    except ValueError as exc:

        raise HTTPException(
            status_code=400,
            detail=str(exc)
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Erro ao salvar assinatura: "
                f"{exc}"
            )
        )
