from fastapi import APIRouter, Request, HTTPException, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from app.services.supabase_client import supabase

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _extract_storage_path(public_url: str) -> str | None:
    marker = "/storage/v1/object/public/processos/"
    if marker in public_url:
        return public_url.split(marker, 1)[1]
    if public_url.startswith("processos/"):
        return public_url.split("processos/", 1)[1]
    return None


def _download_pdf(url: str) -> bytes:
    path = _extract_storage_path(url)
    if not path:
        raise HTTPException(status_code=400, detail="URL de storage invÃ¡lida")

    res = supabase.storage.from_("processos").download(path)
    if hasattr(res, "error") and res.error:
        raise HTTPException(status_code=502, detail=res.error.message)
    if isinstance(res, dict) and res.get("error"):
        raise HTTPException(status_code=502, detail=res.get("error"))
    return res

@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("Index.html", {"request": request})

@router.get("/termo", response_class=HTMLResponse)
def termo(request: Request):
    return templates.TemplateResponse("TermoAceite.html", {"request": request})


@router.get("/ressalvas", response_class=HTMLResponse)
def ressalvas(request: Request):
    return templates.TemplateResponse("Ressalvas.html", {"request": request})


@router.get("/nps", response_class=HTMLResponse)
def nps(request: Request):
    return templates.TemplateResponse("NPS2System.html", {"request": request})


@router.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    processos = []
    try:
        res = (
            supabase
            .table("processos")
            .select(
                "codigo,nome_cliente,empresa,cpf,status,status_entrega,"
                "criado_em,atualizado_em,termo_pdf,pdf_ressalvas,pdf_final,nps_nota"
            )
            .order("criado_em", desc=True)
            .execute()
        )

        if hasattr(res, "error") and res.error:
            raise RuntimeError(res.error.message)

        processos = res.data or []
    except Exception:
        # Fallback para schema antigo (antes das novas colunas)
        res = (
            supabase
            .table("processos")
            .select(
                "codigo,nome_cliente,cpf,status,status_entrega,criado_em,"
                "termo_pdf,pdf_ressalvas,pdf_final"
            )
            .order("criado_em", desc=True)
            .execute()
        )
        processos = res.data or []
        for p in processos:
            p.setdefault("empresa", None)
            p.setdefault("nps_nota", None)
            p.setdefault("atualizado_em", None)

    q = (request.query_params.get("q") or "").strip().lower()
    if q:
        processos = [
            p for p in processos
            if q in (p.get("codigo") or "").lower()
            or q in (p.get("nome_cliente") or "").lower()
            or q in (p.get("empresa") or "").lower()
        ]

    notas = [
        p.get("nps_nota") for p in processos
        if isinstance(p.get("nps_nota"), int)
    ]
    negativas = [n for n in notas if n <= 6]
    neutras = [n for n in notas if 7 <= n <= 8]
    positivas = [n for n in notas if n >= 9]

    def media(valores):
        return round(sum(valores) / len(valores), 2) if valores else None

    stats = {
        "total": len(processos),
        "com_termo": len([p for p in processos if p.get("termo_pdf")]),
        "com_ressalvas": len([p for p in processos if p.get("pdf_ressalvas")]),
        "com_nps": len([p for p in processos if p.get("nps_nota") is not None]),
        "media_negativas": media(negativas),
        "media_neutras": media(neutras),
        "media_positivas": media(positivas),
        "count_negativas": len(negativas),
        "count_neutras": len(neutras),
        "count_positivas": len(positivas)
    }

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "processos": processos,
            "stats": stats,
            "q": q
        }
    )

@router.get("/user", response_class=HTMLResponse)
def user(request: Request):
    return templates.TemplateResponse("User.html", {"request": request})

@router.get("/nps-motor", response_class=HTMLResponse)
def nps_motor(request: Request):
    return templates.TemplateResponse("NPSMotor.html", {"request": request})


@router.get("/pdf/termo/{codigo}")
def pdf_termo(codigo: str):
    proc = (
        supabase
        .table("processos")
        .select("termo_pdf")
        .eq("codigo", codigo)
        .single()
        .execute()
    )
    if not proc.data or not proc.data.get("termo_pdf"):
        raise HTTPException(status_code=404, detail="PDF do termo nÃ£o encontrado")

    pdf_bytes = _download_pdf(proc.data["termo_pdf"])
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=termo.pdf"}
    )


@router.get("/pdf/ressalvas/{codigo}")
def pdf_ressalvas(codigo: str):
    proc = (
        supabase
        .table("processos")
        .select("pdf_ressalvas")
        .eq("codigo", codigo)
        .single()
        .execute()
    )
    if not proc.data or not proc.data.get("pdf_ressalvas"):
        raise HTTPException(status_code=404, detail="PDF de ressalvas nÃ£o encontrado")

    pdf_bytes = _download_pdf(proc.data["pdf_ressalvas"])
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=ressalvas.pdf"}
    )


@router.get("/pdf/final/{codigo}")
def pdf_final(codigo: str):
    proc = (
        supabase
        .table("processos")
        .select("pdf_final")
        .eq("codigo", codigo)
        .single()
        .execute()
    )
    if not proc.data or not proc.data.get("pdf_final"):
        raise HTTPException(status_code=404, detail="PDF final nÃ£o encontrado")

    pdf_bytes = _download_pdf(proc.data["pdf_final"])
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=entrega_final.pdf"}
    )

@router.get("/.well-known/appspecific/com.chrome.devtools.json")
def chrome_devtools():
    return {}
