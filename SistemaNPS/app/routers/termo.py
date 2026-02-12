from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import base64
import hashlib
import os
import re
import random
import string
import uuid
from datetime import datetime
from io import BytesIO
import json

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.lib.utils import ImageReader
import math

from app.services.upload import upload_pdf
from app.services.supabase_client import supabase
from app.services.pdf_layout import draw_header_footer, content_top, content_bottom
from app.services.final_pdf import regenerate_final_pdf_by_codigo


def normalize_base64(encoded: str) -> str:
    encoded = encoded.strip().replace("\n", "").replace("\r", "")
    missing_padding = len(encoded) % 4
    if missing_padding:
        encoded += "=" * (4 - missing_padding)
    return encoded


def gerar_hash_imagem(base64_data: str) -> str:
    _, encoded = base64_data.split(",", 1)
    encoded = normalize_base64(encoded)
    raw = base64.b64decode(encoded)
    return hashlib.sha256(raw).hexdigest()


def _normalizar_itens_imagem(termo_dados: dict | None, imagens: list | None, imagens_existentes: list | None = None) -> list[dict]:
    # Prioriza a lista 'imagens' explícita se fornecida
    itens = imagens if imagens else []
    
    # Fallback para termo_dados['itens'] apenas se 'imagens' estiver vazia
    if not itens and isinstance(termo_dados, dict):
        itens = termo_dados.get("itens") or []

    # Mapeia imagens existentes para preservação
    mapa_existente = {}
    if imagens_existentes and isinstance(imagens_existentes, list):
        for i, img in enumerate(imagens_existentes):
            if isinstance(img, dict):
                key = str(img.get("item")) if img.get("item") is not None else str(i + 1)
                mapa_existente[key] = img

    itens_normalizados = []
    for idx, item in enumerate(itens):
        if not isinstance(item, dict):
            continue
        
        item_val = item.get("item") if item.get("item") is not None else (idx + 1)
        item_key = str(item_val)

        imagem_base64 = item.get("imagem_base64")
        imagem_hash = item.get("imagem_hash")

        if isinstance(imagem_base64, str) and "," in imagem_base64:
            try:
                imagem_hash = gerar_hash_imagem(imagem_base64)
            except Exception:
                imagem_hash = None
        else:
            # Tenta preservar a imagem existente se a nova estiver ausente
            if item_key in mapa_existente:
                existing = mapa_existente[item_key]
                if existing.get("imagem_base64"):
                    imagem_base64 = existing.get("imagem_base64")
                    imagem_hash = existing.get("imagem_hash")

        itens_normalizados.append({
            "item": item_val,
            "regiao_foto": item.get("regiao_foto"),
            "imagem_base64": imagem_base64,
            "imagem_hash": imagem_hash
        })
    return itens_normalizados


def _normalizar_termo_dados(termo_dados: dict | None, imagens: list | None, imagens_existentes: list | None = None) -> dict:
    base = dict(termo_dados or {})
    base["itens"] = _normalizar_itens_imagem(base, imagens, imagens_existentes)
    return base


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
    aprovacao = termo_dados.get("aprovacao") or {}
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
    y += 8
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

    # Fotos (se houver)
    imagens = _normalizar_itens_imagem(termo_dados, data.imagens)
    if imagens:
        imagens = sorted(imagens, key=lambda i: i.get("item", 0))
        gap = 10
        cols = 3
        cell_w = (max_width - gap * (cols - 1)) / cols
        cell_h = 120
        label_h = 12
        rows = int(math.ceil(len(imagens) / cols))
        total_h = 16 + (rows * (cell_h + label_h + gap))

        if y - total_h < content_bottom():
            c.showPage()
            draw_header_footer(c, width, height)
            y = content_top(height)

        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin_x, y, "FOTOS")
        y -= 16

        label_map = {
            "frontal": "Frontal",
            "traseira": "Traseira",
            "lateral-esquerda": "Lateral esquerda",
            "lateral-direita": "Lateral direita",
            "superior": "Superior",
            "inferior": "Inferior",
        }

        start_y = y
        for idx, img_data in enumerate(imagens):
            col = idx % cols
            row = idx // cols
            x = margin_x + col * (cell_w + gap)
            y_top = start_y - row * (cell_h + label_h + gap)

            regiao = img_data.get("regiao_foto")
            label = label_map.get(regiao, regiao or f"Foto {idx + 1}")
            c.setFont("Helvetica-Bold", 9)
            c.drawString(x, y_top, label)

            if img_data.get("imagem_base64"):
                try:
                    _, img_b64 = img_data["imagem_base64"].split(",", 1)
                    img_bytes = base64.b64decode(img_b64)
                    img_reader = ImageReader(BytesIO(img_bytes))
                    c.drawImage(
                        img_reader,
                        x,
                        y_top - label_h - cell_h,
                        width=cell_w,
                        height=cell_h,
                        preserveAspectRatio=True,
                        anchor="c"
                    )
                except Exception:
                    pass

        y = start_y - rows * (cell_h + label_h + gap) - 8

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

    # Final approval (admin area)
    aprovacao_representante = aprovacao.get("representante", "")
    aprovacao_cpf = aprovacao.get("cpf", "")
    if aprovacao_representante or aprovacao_cpf:
        if y < content_bottom():
            c.showPage()
            draw_header_footer(c, width, height)
            y = content_top(height)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin_x, y, "APROVACAO FINAL DO TERMO")
        y -= 18

        y = _draw_label_value(
            c,
            margin_x,
            y,
            max_width,
            "APROVACAO - REPRESENTANTE",
            str(aprovacao_representante),
        )
        y = _draw_label_value(
            c,
            margin_x,
            y,
            max_width,
            "APROVACAO - CPF",
            str(aprovacao_cpf),
        )

router = APIRouter(prefix="/termo", tags=["Termo"])


# ============================================================
# MODEL
# ============================================================

class ImagemTermo(BaseModel):
    item: str | int
    regiao_foto: str | None = None
    imagem_base64: str | None = None
    imagem_hash: str | None = None


class TermoRequest(BaseModel):
    cpf: str
    nome_cliente: str
    empresa: str | None = None
    status_entrega: str
    imagem: str  # base64 (data:image/...)
    imagens: List[ImagemTermo] | list = []
    termo_dados: dict | None = None


class TermoUpdateRequest(BaseModel):
    processo_codigo: str
    cpf: str
    nome_cliente: str
    empresa: str | None = None
    status_entrega: str
    imagem: str
    imagens: List[ImagemTermo] | list = []
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

        termo_dados_normalizado = _normalizar_termo_dados(data.termo_dados, data.imagens)
        data.termo_dados = termo_dados_normalizado
        data.imagens = termo_dados_normalizado.get("itens") or []

        # Converte para dict se vier como objeto Pydantic
        imagens_lista = []
        for img in data.imagens:
            imagens_lista.append(img.dict() if hasattr(img, "dict") else img)

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

        # Upload das imagens individuais para o Storage
        for img_data in imagens_lista:
            if img_data and img_data.get("imagem_base64"):
                try:
                    # A função upload_pdf é genérica e pode lidar com data URIs de imagem
                    upload_pdf(img_data["imagem_base64"], folder)
                except Exception:
                    # Opcional: logar falha, mas continuar o processo
                    pass

        # ====================================================
        # 7. INSERE PROCESSO NO BANCO
        # ====================================================
        res = supabase.table("processos").insert({
            "id": processo_uuid,
            "codigo": codigo_processo,
            "nome_cliente": data.nome_cliente,
            "empresa": data.empresa,
            "cpf": cpf_limpo,
            "status": "TERMO_GERADO",
            "status_entrega": data.status_entrega,
            "termo_pdf": termo_url,
            "imagens_termo": imagens_lista,
            "termo_dados": termo_dados_normalizado,
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
            .select("id, imagens_termo")
            .eq("codigo", data.processo_codigo)
            .single()
            .execute()
        )

        if not proc.data:
            raise HTTPException(status_code=404, detail="Processo não encontrado")

        processo_uuid = proc.data["id"]

        # Carrega imagens existentes para preservação
        imagens_existentes = proc.data.get("imagens_termo")
        if isinstance(imagens_existentes, str):
            try:
                imagens_existentes = json.loads(imagens_existentes)
            except:
                imagens_existentes = []

        # Decode imagem principal
        try:
            _, img_b64 = data.imagem.split(",", 1)
            img_bytes = base64.b64decode(img_b64)
        except Exception:
            raise HTTPException(status_code=400, detail="Falha ao decodificar imagem")

        termo_dados_normalizado = _normalizar_termo_dados(data.termo_dados, data.imagens, imagens_existentes)
        data.termo_dados = termo_dados_normalizado
        data.imagens = termo_dados_normalizado.get("itens") or []

        # Converte para dict se vier como objeto Pydantic
        imagens_lista = []
        for img in data.imagens:
            imagens_lista.append(img.dict() if hasattr(img, "dict") else img)

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

        # Upload das imagens individuais para o Storage
        for img_data in imagens_lista:
            if img_data and img_data.get("imagem_base64"):
                try:
                    # A função upload_pdf é genérica e pode lidar com data URIs de imagem
                    upload_pdf(img_data["imagem_base64"], folder)
                except Exception:
                    # Opcional: logar falha, mas continuar o processo
                    pass

        supabase.table("processos").update({
            "nome_cliente": data.nome_cliente,
            "empresa": data.empresa,
            "cpf": cpf_limpo,
            "status_entrega": data.status_entrega,
            "termo_pdf": termo_url,
            "imagens_termo": imagens_lista,
            "termo_dados": termo_dados_normalizado,
            "atualizado_em": datetime.utcnow().isoformat()
        }).eq("id", processo_uuid).execute()

        # Se o processo ja tiver NPS, reconstroi o PDF final com as alteracoes do admin
        regenerate_final_pdf_by_codigo(data.processo_codigo, set_status_finalizado=False)

        return {"success": True, "processo_id": data.processo_codigo}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno: {str(e)}"
        )
