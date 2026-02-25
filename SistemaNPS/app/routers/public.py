import re
import uuid
from datetime import datetime, timezone
from urllib.parse import quote_plus

from fastapi import APIRouter, Request, HTTPException, Response, Form, status
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from app.services.supabase_client import supabase
from supabase import create_client
import os

router = APIRouter()
templates = Jinja2Templates(directory="app/templates", auto_reload=True)


def _new_supabase_client():
    return create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    )


def _password_is_valid(password: str) -> bool:
    return (
        len(password) >= 6
        and bool(re.search(r"[A-Za-z]", password))
        and bool(re.search(r"\d", password))
    )


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
        raise HTTPException(status_code=400, detail="URL de storage inválida")

    res = supabase.storage.from_("processos").download(path)
    if hasattr(res, "error") and res.error:
        raise HTTPException(status_code=502, detail=res.error.message)
    if isinstance(res, dict) and res.get("error"):
        raise HTTPException(status_code=502, detail=res.get("error"))
    return res


def _extract_project_token(request: Request) -> str:
    return (request.query_params.get("project_token") or request.cookies.get("project_token") or "").strip()


def _token_is_active(token: str) -> bool:
    if not token:
        return False
    res = (
        supabase
        .table("processos")
        .select("project_token,project_token_ativo,project_token_expira_em")
        .eq("project_token", token)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        return False

    row = rows[0]
    if row.get("project_token_ativo") is False:
        return False

    expires = row.get("project_token_expira_em")
    if not expires:
        return True
    try:
        exp_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        return exp_dt >= datetime.now(timezone.utc)
    except Exception:
        return True


def _append_project_token(url: str, token: str) -> str:
    if not token:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}project_token={token}"


def _render_token_required(request: Request, status_code: int = 403):
    return templates.TemplateResponse(
        "User.html",
        {
            "request": request,
            "mensagem_acesso": (
                "Para acessar o sistema, entre pelo link que o Representante "
                "da Fleximedical/Kure enviara para voce."
            ),
        },
        status_code=status_code,
    )

@router.get("/", response_class=HTMLResponse)
def login(request: Request):
    erro = request.query_params.get("erro")
    sucesso = request.query_params.get("sucesso")
    project_token = _extract_project_token(request)
    if not project_token or not _token_is_active(project_token):
        return _render_token_required(request)

    response = templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "erro": erro,
            "sucesso": sucesso,
            "project_token": project_token
        }
    )
    if project_token and _token_is_active(project_token):
        response.set_cookie(
            key="project_token",
            value=project_token,
            max_age=60 * 60 * 24 * 30,
            samesite="lax"
        )
    return response

@router.get("/login", response_class=HTMLResponse)
def login_alias(request: Request):
    erro = request.query_params.get("erro")
    sucesso = request.query_params.get("sucesso")
    project_token = _extract_project_token(request)
    if not project_token or not _token_is_active(project_token):
        return _render_token_required(request)

    response = templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "erro": erro,
            "sucesso": sucesso,
            "project_token": project_token
        }
    )
    if project_token and _token_is_active(project_token):
        response.set_cookie(
            key="project_token",
            value=project_token,
            max_age=60 * 60 * 24 * 30,
            samesite="lax"
        )
    return response


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    project_token: str = Form("")
):
    email = email.strip().lower()
    project_token = (project_token or request.cookies.get("project_token") or "").strip()

    if email == "admin@gmail.com" and password == "Senha123":
        response = RedirectResponse(
            url=_append_project_token("/admin", project_token),
            status_code=status.HTTP_303_SEE_OTHER
        )
        response.set_cookie(
            key="nps_user",
            value=email,
            max_age=60 * 60 * 24 * 30,
            samesite="lax"
        )
        if project_token and _token_is_active(project_token):
            response.set_cookie(
                key="project_token",
                value=project_token,
                max_age=60 * 60 * 24 * 30,
                samesite="lax"
            )
        return response

    if not project_token or not _token_is_active(project_token):
        return _render_token_required(request)

    auth_client = _new_supabase_client()

    try:
        auth_response = auth_client.auth.sign_in_with_password(
            {
                "email": email,
                "password": password
            }
        )
    except Exception:
        auth_response = None

    if not auth_response or not getattr(auth_response, "session", None):
        erro = quote_plus("Email ou senha invalidos.")
        return RedirectResponse(
            url=_append_project_token(f"/login?erro={erro}", project_token),
            status_code=status.HTTP_303_SEE_OTHER
        )

    response = RedirectResponse(
        url=_append_project_token("/index", project_token),
        status_code=status.HTTP_303_SEE_OTHER
    )
    response.set_cookie(
        key="nps_user",
        value=email,
        max_age=60 * 60 * 24 * 30,
        samesite="lax"
    )
    if project_token and _token_is_active(project_token):
        response.set_cookie(
            key="project_token",
            value=project_token,
            max_age=60 * 60 * 24 * 30,
            samesite="lax"
        )
    return response

@router.get("/index", response_class=HTMLResponse)
def index(request: Request):
    project_token = _extract_project_token(request)
    return templates.TemplateResponse(
        "Index.html",
        {"request": request, "project_token": project_token}
    )

@router.get("/cadastro", response_class=HTMLResponse)
def cadastro(request: Request):
    erro = request.query_params.get("erro")
    sucesso = request.query_params.get("sucesso")
    project_token = _extract_project_token(request)
    return templates.TemplateResponse(
        "cadastro.html",
        {
            "request": request,
            "erro": erro,
            "sucesso": sucesso,
            "project_token": project_token
        }
    )


@router.post("/cadastro")
def cadastro_submit(
    request: Request,
    nome: str = Form(...),
    sobrenome: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    project_token: str = Form("")
):
    nome = nome.strip()
    sobrenome = sobrenome.strip()
    email = email.strip().lower()
    project_token = (project_token or request.cookies.get("project_token") or "").strip()
    if not _password_is_valid(password):
        erro = quote_plus("A senha deve ter no minimo 6 caracteres e incluir letras e numeros.")
        return RedirectResponse(
            url=_append_project_token(f"/cadastro?erro={erro}", project_token),
            status_code=status.HTTP_303_SEE_OTHER
        )

    auth_client = _new_supabase_client()

    try:
        auth_client.auth.admin.create_user(
            {
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {
                    "nome": nome,
                    "sobrenome": sobrenome
                }
            }
        )
    except Exception:
        erro = quote_plus("Nao foi possivel concluir o cadastro. Verifique os dados.")
        return RedirectResponse(
            url=_append_project_token(f"/cadastro?erro={erro}", project_token),
            status_code=status.HTTP_303_SEE_OTHER
        )

    return RedirectResponse(
        url=_append_project_token("/login", project_token),
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/esqueci-senha", response_class=HTMLResponse)
def esqueci_senha(request: Request):
    erro = request.query_params.get("erro")
    sucesso = request.query_params.get("sucesso")
    project_token = _extract_project_token(request)
    return templates.TemplateResponse(
        "esqueci_senha.html",
        {
            "request": request,
            "erro": erro,
            "sucesso": sucesso,
            "project_token": project_token
        }
    )


@router.post("/esqueci-senha")
def esqueci_senha_submit(
    request: Request,
    email: str = Form(...),
    project_token: str = Form("")
):
    email = email.strip().lower()
    project_token = (project_token or request.cookies.get("project_token") or "").strip()
    auth_client = _new_supabase_client()
    redirect_to = _append_project_token(str(request.url_for("redefinir_senha")), project_token)

    try:
        auth_client.auth.reset_password_for_email(
            email,
            {"redirect_to": redirect_to}
        )
    except Exception:
        pass

    sucesso = quote_plus(
        "Se o email estiver cadastrado, voce recebera um link para redefinir a senha."
    )
    return RedirectResponse(
        url=_append_project_token(f"/esqueci-senha?sucesso={sucesso}", project_token),
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/redefinir-senha", response_class=HTMLResponse)
def redefinir_senha(request: Request):
    erro = request.query_params.get("erro")
    project_token = _extract_project_token(request)
    return templates.TemplateResponse(
        "redefinir_senha.html",
        {
            "request": request,
            "erro": erro,
            "project_token": project_token
        }
    )


@router.post("/redefinir-senha", response_class=HTMLResponse)
def redefinir_senha_submit(
    request: Request,
    access_token: str = Form(""),
    refresh_token: str = Form(""),
    project_token: str = Form(""),
    password: str = Form(...),
    password_confirm: str = Form(...)
):
    project_token = (project_token or request.cookies.get("project_token") or "").strip()
    if password != password_confirm:
        return templates.TemplateResponse(
            "redefinir_senha.html",
            {
                "request": request,
                "erro": "As senhas nao conferem.",
                "access_token": access_token,
                "refresh_token": refresh_token,
                "project_token": project_token
            }
        )

    if not _password_is_valid(password):
        return templates.TemplateResponse(
            "redefinir_senha.html",
            {
                "request": request,
                "erro": "A nova senha deve ter no minimo 6 caracteres e incluir letras e numeros.",
                "access_token": access_token,
                "refresh_token": refresh_token,
                "project_token": project_token
            }
        )

    if not access_token or not refresh_token:
        return templates.TemplateResponse(
            "redefinir_senha.html",
            {
                "request": request,
                "erro": "Link invalido. Solicite um novo link de recuperacao.",
                "project_token": project_token
            }
        )

    auth_client = _new_supabase_client()
    try:
        auth_client.auth.set_session(access_token, refresh_token)
        auth_client.auth.update_user({"password": password})
    except Exception:
        return templates.TemplateResponse(
            "redefinir_senha.html",
            {
                "request": request,
                "erro": "Nao foi possivel redefinir a senha. Solicite um novo link.",
                "project_token": project_token
            }
        )

    sucesso = quote_plus("Senha atualizada com sucesso. Faca login novamente.")
    return RedirectResponse(
        url=_append_project_token(f"/login?sucesso={sucesso}", project_token),
        status_code=status.HTTP_303_SEE_OTHER
    )

@router.get("/termo", response_class=HTMLResponse)
def termo(request: Request):
    return templates.TemplateResponse(
        "TermoAceite.html",
        {"request": request, "project_token": _extract_project_token(request)}
    )


@router.get("/ressalvas", response_class=HTMLResponse)
def ressalvas(request: Request):
    return templates.TemplateResponse(
        "Ressalvas.html",
        {"request": request, "project_token": _extract_project_token(request)}
    )


@router.get("/nps", response_class=HTMLResponse)
def nps(request: Request):
    return templates.TemplateResponse(
        "NPS2System.html",
        {"request": request, "project_token": _extract_project_token(request)}
    )


@router.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    processos = []
    try:
        res = (
            supabase
            .table("processos")
            .select(
                "codigo,project_token,project_token_ativo,project_token_expira_em,"
                "nome_cliente,empresa,cpf,status,status_entrega,"
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
                "codigo,project_token,nome_cliente,cpf,status,status_entrega,criado_em,"
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
            p.setdefault("project_token", None)

    q = (request.query_params.get("q") or "").strip().lower()
    if q:
        processos = [
            p for p in processos
            if q in (p.get("codigo") or "").lower()
            or q in (p.get("project_token") or "").lower()
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
            "q": q,
            "base_url": str(request.base_url).rstrip("/")
        }
    )


@router.post("/admin/gerar-processo")
def admin_gerar_processo(request: Request):
    process_uuid = str(uuid.uuid4())

    token = ""
    for _ in range(8):
        candidato = uuid.uuid4().hex[:12].upper()
        exists = (
            supabase
            .table("processos")
            .select("id")
            .eq("project_token", candidato)
            .limit(1)
            .execute()
        )
        if not (exists.data or []):
            token = candidato
            break

    if not token:
        raise HTTPException(status_code=500, detail="Nao foi possivel gerar project_token unico")

    res = supabase.table("processos").insert({
        "id": process_uuid,
        "status": "PENDENTE_TERMO",
        "status_entrega": "pendente_admin",
        "project_token": token,
        "project_token_ativo": True,
        "criado_em": datetime.utcnow().isoformat(),
    }).execute()

    if hasattr(res, "error") and res.error:
        raise HTTPException(status_code=500, detail=res.error.message)

    link = _append_project_token(str(request.base_url).rstrip("/") + "/login", token)
    return JSONResponse({"success": True, "project_token": token, "link": link})

@router.get("/user", response_class=HTMLResponse)
def user(request: Request):
    return templates.TemplateResponse(
        "User.html",
        {"request": request, "project_token": _extract_project_token(request)}
    )

@router.get("/nps-motor", response_class=HTMLResponse)
def nps_motor(request: Request):
    return templates.TemplateResponse(
        "NPSMotor.html",
        {"request": request, "project_token": _extract_project_token(request)}
    )


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
        raise HTTPException(status_code=404, detail="PDF do termo não encontrado")

    pdf_bytes = _download_pdf(proc.data["termo_pdf"])
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline; filename=termo.pdf",
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0"
        }
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
        raise HTTPException(status_code=404, detail="PDF de ressalvas não encontrado")

    pdf_bytes = _download_pdf(proc.data["pdf_ressalvas"])
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline; filename=ressalvas.pdf",
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0"
        }
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
        raise HTTPException(status_code=404, detail="PDF final não encontrado")

    pdf_bytes = _download_pdf(proc.data["pdf_final"])
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline; filename=entrega_final.pdf",
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )

@router.get("/.well-known/appspecific/com.chrome.devtools.json")
def chrome_devtools():
    return {}

@router.get("/logout")
def logout():
    response = RedirectResponse(
        url="/login",
        status_code=status.HTTP_303_SEE_OTHER
    )
    # Remove o cookie de autenticação para encerrar a sessão
    response.delete_cookie("nps_user")
    response.delete_cookie("project_token")
    return response
