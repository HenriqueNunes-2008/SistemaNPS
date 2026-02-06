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
from app.services.pdf_layout import draw_header_footer, content_top, content_bottom

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

    c.setFont("Helvetica", 10)
    c.drawString(margem_x, y, f"Processo: {processo_codigo}")
    y -= 15
    c.drawString(margem_x, y, f"Responsável: {responsavel}")
    y -= 15
    c.drawString(
        margem_x,
        y,
        f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    y -= 25

    if observacoes:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(margem_x, y, "Observações:")
        y -= 15
        c.setFont("Helvetica", 10)
        c.drawString(margem_x, y, observacoes)
        y -= 25

    for idx, img in enumerate(imagens, start=1):
        if y < content_bottom():
            c.showPage()
            draw_header_footer(c, largura, altura)
            y = content_top(altura)

        c.setFont("Helvetica-Bold", 11)
        c.drawString(margem_x, y, f"Item {idx}: {img.item}")
        y -= 15

        c.setFont("Helvetica", 10)
        c.drawString(margem_x, y, f"Descrição: {img.descricao}")
        y -= 15

        if img.prazo:
            c.drawString(
                margem_x,
                y,
                f"Prazo: {img.prazo.strftime('%d/%m/%Y')}"
            )
            y -= 15

        c.drawString(
            margem_x,
            y,
            f"Aprovação: {'Sim' if img.aprovacao else 'Não'}"
        )
        y -= 15

        if img.imagem_base64:
            image_stream = decode_base64_image(img.imagem_base64)
            image = ImageReader(image_stream)

            c.drawImage(
                image,
                margem_x,
                y - 150,
                width=200,
                height=150,
                preserveAspectRatio=True,
                mask="auto"
            )
            y -= 170
        else:
            y -= 20

    c.showPage()
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

        return RessalvasResponse(success=True, pdf_url=pdf_url)

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno ao salvar ressalvas: {str(e)}"
        )
