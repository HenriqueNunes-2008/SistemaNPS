from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, date
import base64
import hashlib

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


def _is_admin_request(request: Request) -> bool:
    user_cookie = (request.cookies.get("nps_user") or "").strip().lower()
    if user_cookie == "admin@gmail.com":
        return True
    referer = (request.headers.get("referer") or "").lower()
    return "return=/admin" in referer

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
        if not _is_admin_request(request):
            raise HTTPException(status_code=403, detail="Apenas admin pode criar ressalvas")

        # ----------------------------------------------------
        # 1. BUSCA PROCESSO PELO CÓDIGO (RETORNA UUID REAL)
        # ----------------------------------------------------
        proc = obter_processo_por_identificador(
            data.processo_id,
            "id,codigo,project_token",
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

        supabase.table("processos").update({
            "status": "RESSALVAS_REGISTRADAS",
            "pdf_ressalvas": pdf_url,
            "ressalvas_dados": ressalvas_dados,
            "atualizado_em": datetime.utcnow().isoformat()
        }).eq("id", processo_uuid).execute()

        regenerate_final_pdf_by_codigo(processo_codigo, set_status_finalizado=False)

        return RessalvasResponse(success=True, pdf_url=pdf_url)

    except HTTPException:
        raise


@router.post("/atualizar", response_model=RessalvasResponse)
def atualizar_ressalvas(data: RessalvasUpdateRequest, request: Request):
    try:
        is_admin = _is_admin_request(request)
        proc = obter_processo_por_identificador(
            data.processo_id,
            "id,codigo,project_token,ressalvas_dados",
        )
        processo_uuid = proc["id"]
        processo_codigo = proc.get("codigo") or data.processo_id

        dados_existentes = proc.get("ressalvas_dados")
        if isinstance(dados_existentes, str):
            try:
                import json
                dados_existentes = json.loads(dados_existentes)
            except Exception:
                dados_existentes = {}
        if not isinstance(dados_existentes, dict):
            dados_existentes = {}

        if not is_admin:
            itens_existentes = dados_existentes.get("itens") or []
            if not isinstance(itens_existentes, list) or not itens_existentes:
                raise HTTPException(status_code=400, detail="Nao ha ressalvas do admin para validar")

            aprovacao_por_item = {
                str(img.item): bool(img.aprovacao)
                for img in (data.imagens or [])
            }

            imagens_normalizadas: list[ImagemRessalva] = []
            for idx, item in enumerate(itens_existentes):
                if not isinstance(item, dict):
                    continue
                item_key = str(item.get("item") or (idx + 1))
                imagens_normalizadas.append(
                    ImagemRessalva(
                        item=item_key,
                        descricao=str(item.get("descricao") or ""),
                        prazo=item.get("prazo"),
                        responsavel=item.get("responsavel"),
                        regiao_foto=item.get("regiao_foto"),
                        aprovacao=aprovacao_por_item.get(item_key, bool(item.get("aprovacao"))),
                        imagem_base64=item.get("imagem_base64"),
                    )
                )

            data.imagens = imagens_normalizadas
            data.responsavel = str(dados_existentes.get("responsavel") or "")
            data.cpf = dados_existentes.get("cpf")
            data.observacoes = dados_existentes.get("observacoes")

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
        if is_admin:
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

