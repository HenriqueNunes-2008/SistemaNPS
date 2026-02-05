from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
import os, json
from app.services.supabase_client import supabase
from app.services.upload import upload_pdf


from PyPDF2 import PdfWriter, PdfReader
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

router = APIRouter(prefix="/finalizacao")
templates = Jinja2Templates(directory="app/templates")

@router.post("/gerar-pdf-final")
def gerar_pdf_final(processo_id: str):

    base_dir = os.path.join("pdfs", processo_id)

    termo_pdf = os.path.join(base_dir, "termo", "termo.pdf")
    ressalvas_pdf = os.path.join(base_dir, "ressalvas", "ressalvas.pdf")
    nps_json = os.path.join(base_dir, "nps", "nps.json")
    final_dir = os.path.join(base_dir, "nps-final")

    if not os.path.exists(termo_pdf):
        raise HTTPException(404, "Termo não encontrado")

    if not os.path.exists(ressalvas_pdf):
        raise HTTPException(404, "Ressalvas não encontradas")

    if not os.path.exists(nps_json):
        raise HTTPException(404, "NPS não encontrado")

    os.makedirs(final_dir, exist_ok=True)

    # ===============================
    # CRIA PDF DO NPS
    # ===============================
    nps_pdf_path = os.path.join(final_dir, "nps_temp.pdf")

    with open(nps_json, "r", encoding="utf-8") as f:
        nps = json.load(f)

    c = canvas.Canvas(nps_pdf_path, pagesize=A4)
    width, height = A4

    y = height - 40
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, y, "Pesquisa NPS")
    y -= 40

    c.setFont("Helvetica", 11)
    c.drawString(40, y, f"NPS Final: {nps['nps']}")
    y -= 30

    for k, v in nps["avaliacoes"].items():
        c.drawString(40, y, f"{k.upper()}: {v}")
        y -= 20

    y -= 20
    for titulo, texto in nps["feedback"].items():
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, y, titulo.capitalize())
        y -= 18
        c.setFont("Helvetica", 10)
        c.drawString(40, y, texto[:300])
        y -= 30

    c.showPage()
    c.save()

    # ===============================
    # MERGE FINAL
    # ===============================
    pdf_final = os.path.join(final_dir, "entrega_final.pdf")

    merger = PdfWriter()

    # Add pages from termo_pdf
    with open(termo_pdf, 'rb') as f:
        termo_reader = PdfReader(f)
        for page in termo_reader.pages:
            merger.add_page(page)

    # Add pages from ressalvas_pdf
    with open(ressalvas_pdf, 'rb') as f:
        ressalvas_reader = PdfReader(f)
        for page in ressalvas_reader.pages:
            merger.add_page(page)

    # Add pages from nps_pdf_path
    with open(nps_pdf_path, 'rb') as f:
        nps_reader = PdfReader(f)
        for page in nps_reader.pages:
            merger.add_page(page)

    with open(pdf_final, 'wb') as f:
        merger.write(f)

    # ===============================
    # UPLOAD SUPABASE
    # ===============================
    remote_path = f"{processo_id}/final.pdf"
    final_url = upload_pdf(pdf_final, remote_path)

    # ===============================
    # UPDATE FINAL NO BANCO
    # ===============================
    supabase.table("processos") \
        .update({
            "pdf_final": final_url,
            "status": "finalizado"
        }) \
        .eq("processo_id", processo_id) \
        .execute()

    # ===============================
    # LIMPEZA
    # ===============================
    os.remove(nps_pdf_path)

    return {
        "status": "ok",
        "arquivo": "entrega_final.pdf",
        "url": final_url
    }
