import os
import hmac
import time
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, date
import base64
import hashlib
import json

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from io import BytesIO

from app.services.supabase_client import supabase
from app.services.processo_resolver import obter_processo_por_identificador
from app.services.upload import upload_pdf
from app.services.pdf_layout import draw_header_footer, content_top, content_bottom, draw_wrapped_text
from app.services.final_pdf import regenerate_final_pdf_by_codigo

router = APIRouter(prefix="/ressalvas", tags=["Ressalvas"])


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
    return bool(nps_dados.get("_lock_ressalvas"))

def _is_admin_mode_request(request: Request) -> bool:
    raw = (request.cookies.get("admin_activation_ok") or "").strip()
    if "." not in raw:
        return False
    exp, signature = raw.split(".", 1)
    if not exp.isdigit():
        return False
    secret = (
        os.getenv("ADMIN_ACTIVATION_COOKIE_SECRET")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or ""
    )
    if not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), exp.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    return int(exp) >= int(time.time())


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
    regiao_foto: Optional[str] = None
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
# UTILS
# ============================================================

def normalize_base64(encoded: str) -> str:
    encoded = encoded.strip().replace("\n", "").replace(" ", "")
    missing = len(encoded) % 4
    if missing:
        encoded += "=" * (4 - missing)
    return encoded


def decode_base64_image(base64_data: str) -> BytesIO:
    try:
        if "," not in base64_data:
            raise ValueError("Formato Base64 inválido")

        _, encoded = base64_data.split(",", 1)
        encoded = normalize_base64(encoded)

        return BytesIO(base64.b64decode(encoded))
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Imagem Base64 inválida: {str(e)}"
        )


def gerar_hash_imagem(base64_data: str) -> str:
    _, encoded = base64_data.split(",", 1)
    encoded = normalize_base64(encoded)
    raw = base64.b64decode(encoded)
    return hashlib.sha256(raw).hexdigest()


# ============================================================
# PDF
# ============================================================

def gerar_pdf_ressalvas(
    processo_codigo: str,
    responsavel: str,
    cpf: Optional[str],
    observacoes: Optional[str],
    imagens: List[ImagemRessalva]
) -> BytesIO:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    largura, altura = A4
    margem_x = 40
    max_width = largura - 80

    # Items (Cards)
    cards_per_page = 3
    card_gap = 10

    for i in range(0, len(imagens), cards_per_page):
        if i > 0:
            c.showPage()
        
        chunk = imagens[i : i + cards_per_page]
        is_last_chunk = (i + cards_per_page) >= len(imagens)
        has_aprovacao_final = bool(responsavel or cpf)
        reserva_aprovacao = 56 if is_last_chunk and has_aprovacao_final else 0
        draw_header_footer(c, largura, altura)
        y = content_top(altura)
        
        c.setFont("Helvetica-Bold", 15)
        c.drawString(margem_x, y, f"Itens de Ressalva (Itens {i+1} a {i+len(chunk)})")
        y -= 20
        
        card_h = ((y - (content_bottom() + 20 + reserva_aprovacao)) - (card_gap * (cards_per_page - 1))) / cards_per_page

        for j, item in enumerate(chunk):
            card_top = y - j * (card_h + card_gap)
            c.rect(margem_x, card_top - card_h, max_width, card_h, stroke=1, fill=0)

            # Image
            img_w = 150
            img_h = card_h - 20
            img_x = margem_x + 8
            img_y = card_top - card_h + 10
            c.rect(img_x, img_y, img_w, img_h, stroke=1, fill=0)
            
            if item.imagem_base64:
                try:
                    image_stream = decode_base64_image(item.imagem_base64)
                    c.drawImage(
                        ImageReader(image_stream),
                        img_x + 2,
                        img_y + 2,
                        width=img_w - 4,
                        height=img_h - 4,
                        preserveAspectRatio=True,
                        anchor="c",
                        mask="auto"
                    )
                except Exception:
                    pass
            
            # Text
            tx = img_x + img_w + 8
            ty = card_top - 14
            tw = max_width - (img_w + 24)
            
            c.setFont("Helvetica-Bold", 9)
            c.drawString(tx, ty, f"Item {item.item}: {str(item.descricao)[:55]}")
            ty -= 12
            c.setFont("Helvetica", 8)
            c.drawString(tx, ty, f"Regiao: {str(item.regiao_foto)[:40]}")
            ty -= 11
            
            prazo_str = item.prazo.strftime('%d/%m/%Y') if item.prazo else ""
            c.drawString(tx, ty, f"Prazo: {prazo_str}")
            ty -= 11
            c.drawString(tx, ty, f"Responsavel: {str(item.responsavel)[:35]}")
            ty -= 11

            draw_wrapped_text(
                c,
                f"Descricao: {str(item.descricao)}",
                tx,
                ty,
                tw,
                font_size=8,
                line_height=10,
                max_lines=5,
            )

        if is_last_chunk and has_aprovacao_final:
            apro_y = content_bottom() + 44
            c.setFont("Helvetica-Bold", 10)
            c.drawString(margem_x, apro_y, "Aprovacao final das ressalvas")
            apro_y -= 14
            c.setFont("Helvetica", 9)
            c.drawString(margem_x, apro_y, f"Representante: {responsavel or ''}")
            apro_y -= 12
            c.drawString(margem_x, apro_y, f"CPF: {cpf or ''}")

    if not imagens and (responsavel or cpf):
        draw_header_footer(c, largura, altura)
        y = content_top(altura)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(margem_x, y, "Aprovacao final das ressalvas")
        y -= 24

        c.setFont("Helvetica", 10)
        c.drawString(margem_x, y, f"Representante: {responsavel or ''}")
        y -= 14
        c.drawString(margem_x, y, f"CPF: {cpf or ''}")
    
    c.save()
    buffer.seek(0)
    return buffer


# ============================================================
# ROUTE
# ============================================================

@router.post("/salvar", response_model=RessalvasResponse)
def salvar_ressalvas(data: RessalvasRequest, request: Request):
    try:
        proc = obter_processo_por_identificador(
            data.processo_id,
            "id,codigo,project_token,project_token_ativo,project_token_expira_em,nps_dados,ressalvas_dados",
        )
        processo_uuid = proc["id"]
        processo_codigo = proc.get("codigo") or data.processo_id

        # ----------------------------------------------------
        # 2. GERA PDF
        # ----------------------------------------------------
        pdf_buffer = gerar_pdf_ressalvas(
            processo_codigo=processo_codigo,
            responsavel=data.responsavel,
            cpf=data.cpf,
            observacoes=data.observacoes,
            imagens=data.imagens
        )

        # ----------------------------------------------------
        # 3. PDF → BASE64
        # ----------------------------------------------------
        pdf_base64 = (
            "data:application/pdf;base64,"
            + base64.b64encode(pdf_buffer.read()).decode()
        )

        # ----------------------------------------------------
        # 4. UPLOAD (BUCKET: processos)
        # ----------------------------------------------------
        folder = f"{processo_uuid}/ressalvas"
        pdf_url = upload_pdf(pdf_base64, folder)

        if not pdf_url:
            raise HTTPException(
                status_code=500,
                detail="Falha no upload do PDF"
            )

        # Upload das imagens individuais para o Storage (Igual ao Termo)
        for img in data.imagens:
            if img.imagem_base64:
                try:
                    upload_pdf(img.imagem_base64, folder)
                except Exception:
                    pass

        # ----------------------------------------------------
        # 5. INSERE ITENS DE RESSALVAS
        # ----------------------------------------------------
        itens = []

        for img in data.imagens:
            itens.append({
                "processo_id": processo_uuid,
                "item": img.item,
                "descricao": img.descricao,
                "prazo": img.prazo.isoformat() if img.prazo else None,
                "aprovacao": img.aprovacao,
                "imagem_hash": (
                    gerar_hash_imagem(img.imagem_base64)
                    if img.imagem_base64 else None
                ),
                "criado_em": datetime.utcnow().isoformat()
            })

        if itens:
            supabase.table("ressalvas_itens").insert(itens).execute()

        # ----------------------------------------------------
        # 6. ATUALIZA PROCESSO (NÃO ALTERA criado_em)
        # ----------------------------------------------------
        ressalvas_dados = {
            "responsavel": data.responsavel,
            "cpf": data.cpf,
            "observacoes": data.observacoes,
            "itens": [
                {
                    "item": img.item,
                    "descricao": img.descricao,
                    "prazo": img.prazo.isoformat() if img.prazo else None,
                    "responsavel": img.responsavel,
                    "regiao_foto": img.regiao_foto,
                    "aprovacao": img.aprovacao,
                    "imagem_base64": img.imagem_base64
                }
                for img in data.imagens
            ]
        }

        payload_update = {
            "status": "RESSALVAS_REGISTRADAS",
            "pdf_ressalvas": pdf_url,
            "ressalvas_dados": ressalvas_dados,
            "atualizado_em": datetime.utcnow().isoformat()
        }
        supabase.table("processos").update(payload_update).eq("id", processo_uuid).execute()

        regenerate_final_pdf_by_codigo(processo_codigo, set_status_finalizado=False)

        return RessalvasResponse(success=True, pdf_url=pdf_url)

    except HTTPException:
        raise


@router.post("/atualizar", response_model=RessalvasResponse)
def atualizar_ressalvas(data: RessalvasUpdateRequest, request: Request):
    try:
        proc = obter_processo_por_identificador(
            data.processo_id,
            "id,codigo,project_token,ressalvas_dados,project_token_ativo,project_token_expira_em,nps_dados",
        )
        processo_uuid = proc["id"]

        # Bypass de bloqueio para Admin
        is_admin = _is_admin_mode_request(request)
        if _is_user_edit_locked(proc) and not is_admin:
            raise HTTPException(status_code=403, detail="Edição bloqueada.")

        processo_codigo = proc.get("codigo") or data.processo_id

        dados_existentes = proc.get("ressalvas_dados")
        if isinstance(dados_existentes, str):
            try:
                dados_existentes = json.loads(dados_existentes)
            except Exception:
                dados_existentes = {}
        if not isinstance(dados_existentes, dict):
            dados_existentes = {}


        pdf_buffer = gerar_pdf_ressalvas(
            processo_codigo=processo_codigo,
            responsavel=data.responsavel,
            cpf=data.cpf,
            observacoes=data.observacoes,
            imagens=data.imagens
        )

        pdf_base64 = (
            "data:application/pdf;base64,"
            + base64.b64encode(pdf_buffer.read()).decode()
        )

        folder = f"{processo_uuid}/ressalvas"
        pdf_url = upload_pdf(pdf_base64, folder)

        if not pdf_url:
            raise HTTPException(status_code=500, detail="Falha no upload do PDF")

        # Upload das imagens individuais para o Storage (Igual ao Termo)
        for img in data.imagens:
            if img.imagem_base64:
                try:
                    upload_pdf(img.imagem_base64, folder)
                except Exception:
                    pass

        # Remove itens antigos e reinsere
        supabase.table("ressalvas_itens").delete().eq("processo_id", processo_uuid).execute()

        itens = []
        for img in data.imagens:
            itens.append({
                "processo_id": processo_uuid,
                "item": img.item,
                "descricao": img.descricao,
                "prazo": img.prazo.isoformat() if img.prazo else None,
                "aprovacao": img.aprovacao,
                "imagem_hash": (
                    gerar_hash_imagem(img.imagem_base64)
                    if img.imagem_base64 else None
                ),
                "criado_em": datetime.utcnow().isoformat()
            })

        if itens:
            supabase.table("ressalvas_itens").insert(itens).execute()

        ressalvas_dados = {
            "responsavel": data.responsavel,
            "cpf": data.cpf,
            "observacoes": data.observacoes,
            "itens": [
                {
                    "item": img.item,
                    "descricao": img.descricao,
                    "prazo": img.prazo.isoformat() if img.prazo else None,
                    "responsavel": img.responsavel,
                    "regiao_foto": img.regiao_foto,
                    "aprovacao": img.aprovacao,
                    "imagem_base64": img.imagem_base64
                }
                for img in data.imagens
            ]
        }

        payload_update = {
            "pdf_ressalvas": pdf_url,
            "ressalvas_dados": ressalvas_dados,
            "atualizado_em": datetime.utcnow().isoformat()
        }
        # Sempre atualiza status pois admin esta editando
        payload_update["status"] = "RESSALVAS_REGISTRADAS"

        supabase.table("processos").update(payload_update).eq("id", processo_uuid).execute()

        regenerate_final_pdf_by_codigo(processo_codigo, set_status_finalizado=False)

        return RessalvasResponse(success=True, pdf_url=pdf_url)

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno ao salvar ressalvas: {str(e)}"
        )

@router.get("/dados/{identificador}")
def obter_dados_ressalvas(identificador: str, response: Response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    try:
        proc = obter_processo_por_identificador(
            identificador,
            "id,codigo,project_token,project_token_ativo,project_token_expira_em,ressalvas_dados,nps_dados"
        )
        
        ressalvas_dados = _parse_json_object(proc.get("ressalvas_dados"))
        nps_dados = _parse_json_object(proc.get("nps_dados"))
        
        # Se nao houver dados salvos no JSON, tenta buscar itens da tabela ressalvas_itens
        itens = ressalvas_dados.get("itens")
        if not itens:
            res_itens = (
                supabase.table("ressalvas_itens")
                .select("*")
                .eq("processo_id", proc["id"])
                .execute()
            )
            itens_db = res_itens.data or []
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
