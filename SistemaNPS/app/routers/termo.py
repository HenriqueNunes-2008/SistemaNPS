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
from app.services.processo_resolver import obter_processo_por_identificador
from app.services.supabase_client import supabase
from app.services.pdf_layout import draw_header_footer, content_top, content_bottom, draw_wrapped_text
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


def _gerar_project_token() -> str:
    return uuid.uuid4().hex[:12].upper()


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


def _draw_termo_content(c, width: float, height: float, data) -> None:
    draw_header_footer(c, width, height)
    y = content_top(height)
    x = 40
    max_width = width - 80

    termo_dados = data.termo_dados or {}
    campos = dict(termo_dados.get("campos") or {})
    assinaturas = termo_dados.get("assinaturas") or {}
    aprovacao = termo_dados.get("aprovacao") or {}
    data_info = termo_dados.get("data") or {}

    # --- PAGE 1: INFO ---
    c.setFont("Helvetica-Bold", 16)
    c.drawString(x, y, "TERMO DE ACEITE E ENTREGA DE SERVIÇOS")
    y -= 22
    c.setFont("Helvetica-Oblique", 12)
    c.drawString(x, y, "UNIDADES MÓVEIS")
    y -= 30

    c.setFont("Helvetica-Bold", 11)
    c.drawString(x, y, "Nome do cliente")
    y -= 14
    c.setFont("Helvetica", 10)
    y = draw_wrapped_text(c, data.nome_cliente, x, y, max_width, max_lines=2)
    y -= 4

    c.setFont("Helvetica-Bold", 11)
    c.drawString(x, y, "Empresa")
    y -= 14
    c.setFont("Helvetica", 10)
    y = draw_wrapped_text(c, data.empresa, x, y, max_width, max_lines=2)
    y -= 4

    dia = data_info.get("dia")
    mes = data_info.get("mes")
    ano = data_info.get("ano")
    data_str = f"{dia or ''}/{mes or ''}/{ano or ''}".strip("/")
    if data_str:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(x, y, "Data")
        y -= 14
        c.setFont("Helvetica", 10)
        c.drawString(x, y, data_str)
        y -= 18

    c.setFont("Helvetica", 10)
    for key, value in campos.items():
        k_str = str(key).strip()
        if k_str.upper() in ["NOME DO CLIENTE", "EMPRESA", "REGIÃO DA FOTO"]:
            continue

        if y < content_bottom() + 70:
            c.showPage()
            draw_header_footer(c, width, height)
            y = content_top(height)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x, y, k_str.capitalize()[:80])
        y -= 13
        c.setFont("Helvetica", 10)
        y = draw_wrapped_text(c, str(value), x, y, max_width, max_lines=3)
        y -= 6

    if y > content_bottom() + 75:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x, y, "Status da entrega")
        y -= 13
        status_map = {
            "concluido": "Concluído",
            "concluido_com_ressalva": "Concluído com Ressalva",
        }
        st_label = status_map.get(data.status_entrega, data.status_entrega or "")
        c.setFont("Helvetica", 10)
        c.drawString(x, y, st_label)
        y -= 18

    if y > content_bottom() + 65:
        comprador = assinaturas.get("comprador") or {}
        representante = assinaturas.get("representante") or {}
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x, y, "Assinaturas")
        y -= 13
        c.setFont("Helvetica", 9)
        c.drawString(x, y, f"Comprador: {comprador.get('nome', '')} | CPF: {comprador.get('cpf', '')}")
        y -= 12
        c.drawString(x, y, f"Representante: {representante.get('nome', '')} | CPF: {representante.get('cpf', '')}")
        y -= 14

    if y > content_bottom() + 50:
        rep_aprov = aprovacao.get("representante")
        cpf_aprov = aprovacao.get("cpf")
        if rep_aprov or cpf_aprov:
            c.setFont("Helvetica-Bold", 10)
            c.drawString(x, y, "Aprovacao final")
            y -= 13
            c.setFont("Helvetica", 9)
            c.drawString(x, y, f"Representante: {rep_aprov or ''}")
            y -= 12
            c.drawString(x, y, f"CPF: {cpf_aprov or ''}")

    # --- PAGE 2+: IMAGES ---
    imagens = _normalizar_itens_imagem(termo_dados, data.imagens)
    if not imagens:
        return

    chunk_size = 6
    for i in range(0, len(imagens), chunk_size):
        chunk = imagens[i : i + chunk_size]
        c.showPage()
        draw_header_footer(c, width, height)
        y = content_top(height)
        
        c.setFont("Helvetica-Bold", 15)
        c.drawString(x, y, f"Fotos do termo (Página {i//6 + 1})")
        
        cols = 3
        cols = 2
        rows = 3
        gap_x = 12
        gap_y = 14
        top_y = y - 20
        cell_w = (max_width - gap_x) / cols
        cell_h = ((top_y - (content_bottom() + 20)) - (gap_y * (rows - 1))) / rows

        label_map = {
            "frontal": "Frontal",
            "traseira": "Traseira",
            "lateral-esquerda": "Lateral esquerda",
            "lateral-direita": "Lateral direita",
            "superior": "Superior",
            "inferior": "Inferior",
        }

        for idx, img_data in enumerate(chunk):
            col = idx % cols
            row = idx // cols
            cx = x + (col * (cell_w + gap_x))
            cy_top = top_y - (row * (cell_h + gap_y))
            img_y = cy_top - cell_h + 2

            regiao = img_data.get("regiao_foto")
            label = label_map.get(regiao, regiao or f"Foto {i + idx + 1}")
            
            c.setFont("Helvetica-Bold", 9)
            c.drawString(cx, cy_top, label)
            c.rect(cx, img_y, cell_w, cell_h - 12, stroke=1, fill=0)

            b64 = img_data.get("imagem_base64")
            if b64 and "," in b64:
                try:
                    _, raw = b64.split(",", 1)
                    img_bytes = base64.b64decode(raw)
                    c.drawImage(
                        ImageReader(BytesIO(img_bytes)),
                        cx + 3,
                        img_y + 3,
                        width=cell_w - 6,
                        height=cell_h - 18,
                        preserveAspectRatio=True,
                        anchor="c",
                        mask="auto"
                    )
                except Exception:
                    pass
            else:
                c.setFont("Helvetica", 8)
                c.drawString(cx + 6, img_y + (cell_h / 2) - 6, "Imagem nao informada")

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
        project_token = _gerar_project_token()

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
            "project_token": project_token,
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
            "processo_id": project_token,
            "codigo": codigo_processo,
            "project_token": project_token
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
            raise HTTPException(status_code=400, detail="CPF invalido")

        if not data.nome_cliente.strip():
            raise HTTPException(status_code=400, detail="Nome do cliente obrigatorio")

        if "," not in data.imagem:
            raise HTTPException(status_code=400, detail="Imagem Base64 invalida")

        if data.status_entrega not in ("concluido", "concluido_com_ressalva"):
            raise HTTPException(status_code=400, detail="Status de entrega invalido")

        proc = obter_processo_por_identificador(
            data.processo_codigo,
            "id,codigo,project_token,imagens_termo",
        )
        processo_uuid = proc["id"]
        processo_codigo = proc.get("codigo")
        if not processo_codigo:
            primeiro_nome = re.sub(r"[^A-Z]", "", data.nome_cliente.split()[0].upper())
            ultimos_cpf = cpf_limpo[-3:]
            data_hoje = datetime.now().strftime("%Y-%m-%d")
            sufixo = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
            processo_codigo = f"{primeiro_nome}_{ultimos_cpf}_{data_hoje}_{sufixo}"
        project_token = proc.get("project_token")

        imagens_existentes = proc.get("imagens_termo")
        if isinstance(imagens_existentes, str):
            try:
                imagens_existentes = json.loads(imagens_existentes)
            except Exception:
                imagens_existentes = []

        try:
            _, img_b64 = data.imagem.split(",", 1)
            _ = base64.b64decode(img_b64)
        except Exception:
            raise HTTPException(status_code=400, detail="Falha ao decodificar imagem")

        termo_dados_normalizado = _normalizar_termo_dados(data.termo_dados, data.imagens, imagens_existentes)
        data.termo_dados = termo_dados_normalizado
        data.imagens = termo_dados_normalizado.get("itens") or []

        imagens_lista = []
        for img in data.imagens:
            imagens_lista.append(img.dict() if hasattr(img, "dict") else img)

        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        draw_header_footer(c, width, height)
        _draw_termo_content(c, width, height, data)
        c.showPage()
        c.save()
        buffer.seek(0)

        pdf_base64 = "data:application/pdf;base64," + base64.b64encode(buffer.read()).decode()
        folder = f"{processo_uuid}/termo"
        termo_url = upload_pdf(pdf_base64, folder)
        if not termo_url:
            raise HTTPException(status_code=500, detail="Falha no upload do PDF")

        for img_data in imagens_lista:
            if img_data and img_data.get("imagem_base64"):
                try:
                    upload_pdf(img_data["imagem_base64"], folder)
                except Exception:
                    pass

        supabase.table("processos").update({
            "codigo": processo_codigo,
            "nome_cliente": data.nome_cliente,
            "empresa": data.empresa,
            "cpf": cpf_limpo,
            "status_entrega": data.status_entrega,
            "termo_pdf": termo_url,
            "imagens_termo": imagens_lista,
            "termo_dados": termo_dados_normalizado,
            "atualizado_em": datetime.utcnow().isoformat()
        }).eq("id", processo_uuid).execute()

        regenerate_final_pdf_by_codigo(processo_codigo, set_status_finalizado=False)
        return {
            "success": True,
            "processo_id": project_token or processo_codigo,
            "codigo": processo_codigo,
            "project_token": project_token
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")
