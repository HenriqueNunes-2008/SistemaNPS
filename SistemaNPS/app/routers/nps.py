from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import date
from io import BytesIO
import base64

import httpx
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from PyPDF2 import PdfMerger

from app.services.upload import upload_pdf
from app.services.supabase_client import supabase

router = APIRouter(prefix="/nps", tags=["NPS"])


# ===============================
# MODELS
# ===============================
class NPSRequest(BaseModel):
    processo_id: str
    nps: int
    avaliacoes: dict
    feedback: dict


class NPSUpdateRequest(BaseModel):
    processo_id: str
    nps: int
    avaliacoes: dict
    feedback: dict


# ===============================
# ROTA
# ===============================
@router.post("/finalizar")
def finalizar_nps(data: NPSRequest):
    try:
        processo_id = data.processo_id.strip()
        if not processo_id:
            raise HTTPException(status_code=400, detail="processo_id ausente")

        # ===============================
        # BUSCA PROCESSO + PDFs
        # ===============================
        proc = (
            supabase
            .table("processos")
            .select("id,termo_pdf,pdf_ressalvas")
            .eq("codigo", processo_id)
            .single()
            .execute()
        )

        if not proc.data:
            raise HTTPException(status_code=404, detail="Processo não encontrado")

        processo_uuid = proc.data["id"]
        termo_pdf_url = proc.data.get("termo_pdf")
        ressalvas_pdf_url = proc.data.get("pdf_ressalvas")

        if not termo_pdf_url:
            raise HTTPException(status_code=404, detail="Termo não encontrado")

        def extract_storage_path(public_url: str) -> str | None:
            marker = "/storage/v1/object/public/processos/"
            if marker not in public_url:
                return None
            return public_url.split(marker, 1)[1]

        def download_pdf(url: str) -> bytes:
            resp = httpx.get(url, timeout=30)
            resp.raise_for_status()
            return resp.content

        try:
            termo_bytes = download_pdf(termo_pdf_url)
            ressalvas_bytes = None
            if ressalvas_pdf_url:
                ressalvas_bytes = download_pdf(ressalvas_pdf_url)
        except Exception:
            # Fallback para bucket privado: usa download via Supabase
            termo_path = extract_storage_path(termo_pdf_url)
            ressalvas_path = extract_storage_path(ressalvas_pdf_url) if ressalvas_pdf_url else None
            try:
                if not termo_path:
                    raise Exception("URL de storage inválida")
                termo_res = supabase.storage.from_("processos").download(termo_path)
                if hasattr(termo_res, "error") and termo_res.error:
                    raise Exception(termo_res.error.message)

                termo_bytes = termo_res
                ressalvas_bytes = None
                if ressalvas_path:
                    ressalvas_res = supabase.storage.from_("processos").download(ressalvas_path)
                    if hasattr(ressalvas_res, "error") and ressalvas_res.error:
                        raise Exception(ressalvas_res.error.message)
                    ressalvas_bytes = ressalvas_res
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Falha ao baixar PDFs: {str(e)}")

        # ===============================
        # GERAR PDF NPS (EM MEMORIA)
        # ===============================
        nps_buffer = BytesIO()
        c = canvas.Canvas(nps_buffer, pagesize=A4)
        width, height = A4

        y = height - 50
        c.setFont("Helvetica-Bold", 16)
        c.drawString(40, y, "Pesquisa de Satisfação (NPS)")
        y -= 40

        c.setFont("Helvetica", 12)
        c.drawString(40, y, f"NPS informado: {data.nps}")
        y -= 30

        # Avaliações
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, y, "Avaliações")
        y -= 20

        c.setFont("Helvetica", 10)
        for k, v in data.avaliacoes.items():
            c.drawString(40, y, f"{k}: {v}")
            y -= 15
            if y < 80:
                c.showPage()
                y = height - 50
                c.setFont("Helvetica", 10)

        # Feedback
        y -= 20
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, y, "Feedback")
        y -= 20

        c.setFont("Helvetica", 10)
        for titulo, texto in data.feedback.items():
            c.drawString(40, y, f"{titulo}:")
            y -= 14

            for linha in texto.split("\n"):
                c.drawString(50, y, linha[:110])
                y -= 14
                if y < 80:
                    c.showPage()
                    y = height - 50
                    c.setFont("Helvetica", 10)

            y -= 10

        c.showPage()
        c.save()
        nps_buffer.seek(0)

        # ===============================
        # MERGE FINAL (2 OU 3 PDFs)
        # ===============================
        merger = PdfMerger()
        merger.append(BytesIO(termo_bytes))
        if ressalvas_bytes:
            merger.append(BytesIO(ressalvas_bytes))
        merger.append(nps_buffer)
        final_buffer = BytesIO()
        merger.write(final_buffer)
        merger.close()
        final_buffer.seek(0)

        # ===============================
        # UPLOAD
        # ===============================
        final_base64 = (
            "data:application/pdf;base64,"
            + base64.b64encode(final_buffer.read()).decode()
        )
        final_url = upload_pdf(final_base64, f"{processo_uuid}/final")

        if not final_url:
            raise HTTPException(500, "Falha no upload do PDF final")

        # ===============================
        # UPDATE BANCO
        # ===============================
        supabase.table("processos").update({
            "status": "finalizado",
            "pdf_final": final_url,
            "nps_dados": {
                "nps": data.nps,
                "avaliacoes": data.avaliacoes,
                "feedback": data.feedback
            },
            "nps_nota": data.nps,
            "finalizado_em": date.today().isoformat()
        }).eq("id", processo_uuid).execute()

        return {
            "status": "ok",
            "pdf_final": final_url
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@router.post("/atualizar")
def atualizar_nps(data: NPSUpdateRequest):
    processo_id = data.processo_id.strip()
    if not processo_id:
        raise HTTPException(status_code=400, detail="processo_id ausente")

    proc = (
        supabase
        .table("processos")
        .select("id")
        .eq("codigo", processo_id)
        .single()
        .execute()
    )

    if not proc.data:
        raise HTTPException(status_code=404, detail="Processo nÃ£o encontrado")

    processo_uuid = proc.data["id"]

    supabase.table("processos").update({
        "nps_dados": {
            "nps": data.nps,
            "avaliacoes": data.avaliacoes,
            "feedback": data.feedback
        },
        "nps_nota": data.nps,
        "atualizado_em": date.today().isoformat()
    }).eq("id", processo_uuid).execute()

    return {"status": "ok"}
