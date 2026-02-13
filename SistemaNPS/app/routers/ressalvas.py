from fastapi import APIRouter, HTTPException
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
from app.services.upload import upload_pdf
from app.services.pdf_layout import draw_header_footer, content_top, content_bottom, draw_wrapped_text
from app.services.final_pdf import regenerate_final_pdf_by_codigo

router = APIRouter(prefix="/ressalvas", tags=["Ressalvas"])

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
    processo_id: str  # CÓDIGO HUMANO (ex: EDIVALDO_819_2026-01-27_7N26)
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
    observacoes: Optional[str],
    imagens: List[ImagemRessalva]
) -> BytesIO:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    largura, altura = A4
    margem_x = 40
    draw_header_footer(c, largura, altura)
    y = content_top(altura)

    c.setFont("Helvetica-Bold", 14)
    c.drawString(margem_x, y, "RELATÓRIO DE RESSALVAS")
    y -= 30
    max_width = largura - 80

    c.setFont("Helvetica-Bold", 11)
    c.drawString(margem_x, y, "Processo")
    y -= 14
    c.setFont("Helvetica", 10)
    c.drawString(margem_x, y, f"Processo: {processo_codigo}")
    y -= 18

    c.setFont("Helvetica-Bold", 11)
    c.drawString(margem_x, y, "Responsável")
    y -= 14
    c.setFont("Helvetica", 10)
    c.drawString(margem_x, y, f"Responsável: {responsavel}")
    y -= 18

    c.setFont("Helvetica-Bold", 11)
    c.drawString(margem_x, y, "Data")
    y -= 14
    c.setFont("Helvetica", 10)
    c.drawString(
        margem_x,
        y,
        f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    y -= 18

    if observacoes:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(margem_x, y, "Observações:")
        y -= 14
        c.setFont("Helvetica", 10)
        y = draw_wrapped_text(c, observacoes, margem_x, y, max_width, max_lines=10)
        y -= 18

    # Items (Cards)
    cards_per_page = 3
    card_gap = 10

    for i in range(0, len(imagens), cards_per_page):
        c.showPage()
        
        chunk = imagens[i : i + cards_per_page]
        draw_header_footer(c, largura, altura)
        y = content_top(altura)
        
        c.setFont("Helvetica-Bold", 15)
        c.drawString(margem_x, y, f"Itens de Ressalva (Itens {i+1} a {i+len(chunk)})")
        y -= 20
        
        card_h = ((y - (content_bottom() + 20)) - (card_gap * (cards_per_page - 1))) / cards_per_page

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
            c.drawString(tx, ty, f"Aprovacao: {'Sim' if item.aprovacao else 'Nao'}")
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
    
    c.save()
    buffer.seek(0)
    return buffer


# ============================================================
# ROUTE
# ============================================================

@router.post("/salvar", response_model=RessalvasResponse)
def salvar_ressalvas(data: RessalvasRequest):
    try:
        # ----------------------------------------------------
        # 1. BUSCA PROCESSO PELO CÓDIGO (RETORNA UUID REAL)
        # ----------------------------------------------------
        proc = (
            supabase
            .table("processos")
            .select("id")
            .eq("codigo", data.processo_id)
            .single()
            .execute()
        )

        if not proc.data:
            raise HTTPException(
                status_code=404,
                detail=f"Processo não encontrado: {data.processo_id}"
            )

        processo_uuid = proc.data["id"]

        # ----------------------------------------------------
        # 2. GERA PDF
        # ----------------------------------------------------
        pdf_buffer = gerar_pdf_ressalvas(
            processo_codigo=data.processo_id,
            responsavel=data.responsavel,
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

        regenerate_final_pdf_by_codigo(data.processo_id, set_status_finalizado=False)

        return RessalvasResponse(success=True, pdf_url=pdf_url)

    except HTTPException:
        raise


@router.post("/atualizar", response_model=RessalvasResponse)
def atualizar_ressalvas(data: RessalvasUpdateRequest):
    try:
        proc = (
            supabase
            .table("processos")
            .select("id")
            .eq("codigo", data.processo_id)
            .single()
            .execute()
        )

        if not proc.data:
            raise HTTPException(
                status_code=404,
                detail=f"Processo não encontrado: {data.processo_id}"
            )

        processo_uuid = proc.data["id"]

        pdf_buffer = gerar_pdf_ressalvas(
            processo_codigo=data.processo_id,
            responsavel=data.responsavel,
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

        supabase.table("processos").update({
            "status": "RESSALVAS_REGISTRADAS",
            "pdf_ressalvas": pdf_url,
            "ressalvas_dados": ressalvas_dados,
            "atualizado_em": datetime.utcnow().isoformat()
        }).eq("id", processo_uuid).execute()

        regenerate_final_pdf_by_codigo(data.processo_id, set_status_finalizado=False)

        return RessalvasResponse(success=True, pdf_url=pdf_url)

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno ao salvar ressalvas: {str(e)}"
        )
