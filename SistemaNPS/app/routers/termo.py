from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import base64
import os
import re
import random
import string
import uuid
from datetime import datetime
from io import BytesIO

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth

from app.services.upload import upload_pdf
from app.services.supabase_client import supabase
from app.services.pdf_layout import draw_header_footer, content_top, content_bottom


def _wrap_text(text: str, max_width: float, font_name: str, font_size: int) -> list[str]:
    if not text:
        return [""]
    words = str(text).split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if stringWidth(test, font_name, font_size) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current or not lines:
        lines.append(current)
    return lines


def _draw_label_value(
    c,
    x: float,
    y: float,
    max_width: float,
    label: str,
    value: str,
    font_label: str = "Helvetica-Bold",
    font_value: str = "Helvetica",
    size_label: int = 11,
    size_value: int = 11,
    line_height: int = 14
) -> float:
    c.setFont(font_label, size_label)
    c.drawString(x, y, label)
    y -= line_height
    c.setFont(font_value, size_value)
    for line in _wrap_text(value, max_width, font_value, size_value):
        c.drawString(x, y, line)
        y -= line_height
    y -= 8
    return y


def _draw_termo_content(c, width: float, height: float, data) -> None:
    margin_x = 40
    max_width = width - (margin_x * 2)
    termo_dados = data.termo_dados or {}
    campos = dict(termo_dados.get("campos") or {})
    assinaturas = termo_dados.get("assinaturas") or {}
    data_info = termo_dados.get("data") or {}

    if data.nome_cliente and "NOME DO CLIENTE" not in campos:
        campos["NOME DO CLIENTE"] = data.nome_cliente
    if data.empresa and "EMPRESA" not in campos:
        campos["EMPRESA"] = data.empresa

    fields_order = [
        "NOME DO CLIENTE",
        "EMPRESA",
        "PRODUTO E CÓDIGO DA ENTREGA",
        "RESPONSÁVEL PELA ENTREGA",
        "QUEM REALIZOU O ATENDIMENTO?",
        "LOCAL DA ENTREGA",
    ]

    y = content_top(height)

    # Title
    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin_x, y, "TERMO DE ACEITE E ENTREGA DE SERVIÇOS")
    y -= 22
    c.setFont("Helvetica-Oblique", 12)
    c.drawString(margin_x, y, "UNIDADES MÓVEIS")
    y -= 22

    # Date
    dia = data_info.get("dia")
    mes = data_info.get("mes")
    ano = data_info.get("ano")
    if dia or mes or ano:
        data_str = f"{dia or ''}/{mes or ''}/{ano or ''}".strip("/")
    else:
        data_str = ""
    y = _draw_label_value(c, margin_x, y, max_width, "DATA", data_str)

    # Fields
    for key in fields_order:
        if key in campos:
            if y < content_bottom():
                c.showPage()
                draw_header_footer(c, width, height)
                y = content_top(height)
            y = _draw_label_value(c, margin_x, y, max_width, key, str(campos.get(key, "")))

    # Any extra fields
    for key, value in campos.items():
        if key in fields_order:
            continue
        if y < content_bottom():
            c.showPage()
            draw_header_footer(c, width, height)
            y = content_top(height)
        y = _draw_label_value(c, margin_x, y, max_width, key, str(value))

    # Status
    status_map = {
        "concluido": "Concluído",
        "concluido_com_ressalva": "Concluído com Ressalva",
    }
    status_label = status_map.get(data.status_entrega, data.status_entrega or "")
    if status_label:
        if y < content_bottom():
            c.showPage()
            draw_header_footer(c, width, height)
            y = content_top(height)
        y = _draw_label_value(c, margin_x, y, max_width, "STATUS DA ENTREGA", status_label)

    # Signatures
    comprador = assinaturas.get("comprador") or {}
    representante = assinaturas.get("representante") or {}
    assinatura_lines = [
        ("COMPRADOR - NOME", comprador.get("nome", "")),
        ("COMPRADOR - CPF", comprador.get("cpf", "")),
        ("REPRESENTANTE COMERCIAL - NOME", representante.get("nome", "")),
        ("REPRESENTANTE COMERCIAL - CPF", representante.get("cpf", "")),
    ]

    if y < content_bottom():
        c.showPage()
        draw_header_footer(c, width, height)
        y = content_top(height)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin_x, y, "ASSINATURAS")
    y -= 18

    for label, value in assinatura_lines:
        if y < content_bottom():
            c.showPage()
            draw_header_footer(c, width, height)
            y = content_top(height)
        y = _draw_label_value(c, margin_x, y, max_width, label, value)

router = APIRouter(prefix="/termo", tags=["Termo"])


# ============================================================
# MODEL
# ============================================================

class TermoRequest(BaseModel):
    cpf: str
    nome_cliente: str
    empresa: str | None = None
    status_entrega: str
    imagem: str  # base64 (data:image/...)
    imagens: list = []  # list of dicts with item and imagem_base64
    termo_dados: dict | None = None


class TermoUpdateRequest(BaseModel):
    processo_codigo: str
    cpf: str
    nome_cliente: str
    empresa: str | None = None
    status_entrega: str
    imagem: str
    imagens: list = []
    termo_dados: dict | None = None


# ============================================================
# ROTA
# ============================================================

@router.post("/salvar")
def salvar_termo(data: TermoRequest):
    try:
        # ====================================================
        # 1. VALIDAÇÕES
        # ====================================================
        cpf_limpo = re.sub(r"\D", "", data.cpf)
        if not re.fullmatch(r"\d{11}", cpf_limpo):
            raise HTTPException(status_code=400, detail="CPF inválido")

        if not data.nome_cliente.strip():
            raise HTTPException(status_code=400, detail="Nome do cliente obrigatório")

        if "," not in data.imagem:
            raise HTTPException(status_code=400, detail="Imagem Base64 inválida")

        if data.status_entrega not in ("concluido", "concluido_com_ressalva"):
            raise HTTPException(status_code=400, detail="Status de entrega inválido")

        # ====================================================
        # 2. GERA CÓDIGO HUMANO + UUID REAL
        # ====================================================
        primeiro_nome = re.sub(r"[^A-Z]", "", data.nome_cliente.split()[0].upper())
        ultimos_cpf = cpf_limpo[-3:]
        data_hoje = datetime.now().strftime("%Y-%m-%d")
        sufixo = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))

        codigo_processo = f"{primeiro_nome}_{ultimos_cpf}_{data_hoje}_{sufixo}"
        processo_uuid = str(uuid.uuid4())  # ✅ UUID REAL (IMPORTANTE)

        # ====================================================
        # 4. GERA PDF EM MEMÓRIA
        # ====================================================
        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        # PDF do termo com dados informados
        draw_header_footer(c, width, height)
        _draw_termo_content(c, width, height, data)

        c.showPage()
        c.save()
        buffer.seek(0)

        # ====================================================
        # 5. PDF → BASE64
        # ====================================================
        pdf_base64 = (
            "data:application/pdf;base64,"
            + base64.b64encode(buffer.read()).decode()
        )

        # ====================================================
        # 6. UPLOAD (BUCKET: processos)
        # ====================================================
        folder = f"{processo_uuid}/termo"
        termo_url = upload_pdf(pdf_base64, folder)

        if not termo_url:
            raise HTTPException(
                status_code=500,
                detail="Falha no upload do PDF"
            )

        # ====================================================
        # 7. UPLOAD IMAGENS ADICIONAIS (SE HOUVER)
        # ====================================================
        imagens_urls = []
        if data.imagens:
            for img_data in data.imagens:
                try:
                    _, img_b64 = img_data["imagem_base64"].split(",", 1)
                    img_bytes = base64.b64decode(img_b64)
                    img_buffer = BytesIO(img_bytes)
                    img_base64 = (
                        "data:image/png;base64,"
                        + base64.b64encode(img_buffer.read()).decode()
                    )
                    img_folder = f"{processo_uuid}/termo/imagens"
                    img_url = upload_pdf(img_base64, img_folder)  # reuse upload_pdf for images
                    if img_url:
                        imagens_urls.append({
                            "item": img_data["item"],
                            "url": img_url
                        })
                except Exception as e:
                    print(f"Erro ao processar imagem {img_data['item']}: {e}")

        # ====================================================
        # 8. INSERE PROCESSO NO BANCO
        # ====================================================
        res = supabase.table("processos").insert({
            "processo_id": processo_uuid,     # ✅ UUID REAL
            "codigo": codigo_processo,        # ✅ CÓDIGO HUMANO
            "nome_cliente": data.nome_cliente,
            "empresa": data.empresa,
            "cpf": cpf_limpo,
            "status": "TERMO_GERADO",
            "status_entrega": data.status_entrega,
            "termo_pdf": termo_url,
            "imagens_termo": imagens_urls if imagens_urls else None,
            "termo_dados": data.termo_dados,
            "criado_em": datetime.utcnow().isoformat()
        }).execute()

        if hasattr(res, "error") and res.error:
            raise HTTPException(
                status_code=500,
                detail=f"Erro Supabase: {res.error.message}"
            )

        # ====================================================
        # 8. RESPOSTA
        # ====================================================
        return {
            "success": True,
            "processo_id": codigo_processo
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno: {str(e)}"
        )


@router.post("/atualizar")
def atualizar_termo(data: TermoUpdateRequest):
    try:
        cpf_limpo = re.sub(r"\D", "", data.cpf)
        if not re.fullmatch(r"\d{11}", cpf_limpo):
            raise HTTPException(status_code=400, detail="CPF inválido")

        if not data.nome_cliente.strip():
            raise HTTPException(status_code=400, detail="Nome do cliente obrigatório")

        if "," not in data.imagem:
            raise HTTPException(status_code=400, detail="Imagem Base64 inválida")

        if data.status_entrega not in ("concluido", "concluido_com_ressalva"):
            raise HTTPException(status_code=400, detail="Status de entrega inválido")

        proc = (
            supabase
            .table("processos")
            .select("id")
            .eq("codigo", data.processo_codigo)
            .single()
            .execute()
        )

        if not proc.data:
            raise HTTPException(status_code=404, detail="Processo não encontrado")

        processo_uuid = proc.data["id"]

        # Decode imagem principal
        try:
            _, img_b64 = data.imagem.split(",", 1)
            img_bytes = base64.b64decode(img_b64)
        except Exception:
            raise HTTPException(status_code=400, detail="Falha ao decodificar imagem")

        # Gera PDF em memória
        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        # PDF do termo com dados informados
        draw_header_footer(c, width, height)
        _draw_termo_content(c, width, height, data)

        c.showPage()
        c.save()
        buffer.seek(0)

        pdf_base64 = (
            "data:application/pdf;base64,"
            + base64.b64encode(buffer.read()).decode()
        )

        folder = f"{processo_uuid}/termo"
        termo_url = upload_pdf(pdf_base64, folder)

        if not termo_url:
            raise HTTPException(status_code=500, detail="Falha no upload do PDF")

        # Upload imagens adicionais (se houver)
        imagens_urls = []
        if data.imagens:
            for img_data in data.imagens:
                try:
                    _, img_b64 = img_data["imagem_base64"].split(",", 1)
                    img_bytes = base64.b64decode(img_b64)
                    img_buffer = BytesIO(img_bytes)
                    img_base64 = (
                        "data:image/png;base64,"
                        + base64.b64encode(img_buffer.read()).decode()
                    )
                    img_folder = f"{processo_uuid}/termo/imagens"
                    img_url = upload_pdf(img_base64, img_folder)
                    if img_url:
                        imagens_urls.append({
                            "item": img_data["item"],
                            "url": img_url
                        })
                except Exception as e:
                    print(f"Erro ao processar imagem {img_data['item']}: {e}")

        supabase.table("processos").update({
            "nome_cliente": data.nome_cliente,
            "empresa": data.empresa,
            "cpf": cpf_limpo,
            "status_entrega": data.status_entrega,
            "termo_pdf": termo_url,
            "imagens_termo": imagens_urls if imagens_urls else None,
            "termo_dados": data.termo_dados,
            "atualizado_em": datetime.utcnow().isoformat()
        }).eq("id", processo_uuid).execute()

        return {"success": True, "processo_id": data.processo_codigo}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno: {str(e)}"
        )
