import re
import uuid
import hmac
import time
import base64
import hashlib
import json
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
    # Tenta extrair token de varios parametros possiveis para robustez
    token = request.query_params.get("project_token")
    if not token:
        token = request.query_params.get("processo")
    if not token:
        token = request.query_params.get("id")
    if not token:
        token = request.cookies.get("project_token")
    return (token or "").strip()


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


def _get_admin_cookie_secret() -> str:
    return (
        os.getenv("ADMIN_ACTIVATION_COOKIE_SECRET")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or ""
    )


def _get_admin_activation_hash() -> str:
    try:
        res = (
            supabase
            .table("configuracoes_seguras")
            .select("valor_hash")
            .eq("chave", "admin_activation_hash")
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if rows:
            hash_value = str(rows[0].get("valor_hash") or "").strip()
            if hash_value:
                return hash_value
    except Exception:
        pass

    return (os.getenv("ADMIN_ACTIVATION_HASH") or "").strip()


def _verify_admin_activation_password(password: str) -> bool:
    """
    Expected format for ADMIN_ACTIVATION_HASH:
    pbkdf2_sha256$<iterations>$<salt_base64>$<hash_base64>
    """
    encoded = _get_admin_activation_hash()
    if not encoded:
        return False

    parts = encoded.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    _, iter_str, salt_b64, hash_b64 = parts

    try:
        iterations = int(iter_str)
        salt = base64.b64decode(salt_b64.encode("utf-8"))
        expected = base64.b64decode(hash_b64.encode("utf-8"))
    except Exception:
        return False

    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=len(expected),
    )
    return hmac.compare_digest(derived, expected)


def _encode_admin_activation_password(password: str, iterations: int = 390000) -> str:
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=32,
    )
    salt_b64 = base64.b64encode(salt).decode("utf-8")
    hash_b64 = base64.b64encode(derived).decode("utf-8")
    return f"pbkdf2_sha256${iterations}${salt_b64}${hash_b64}"


def _build_admin_activation_cookie(max_age_seconds: int = 600) -> str:
    exp = str(int(time.time()) + max_age_seconds)
    secret = _get_admin_cookie_secret().encode("utf-8")
    signature = hmac.new(secret, exp.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{exp}.{signature}"


def _is_admin_activation_granted(request: Request) -> bool:
    raw = (request.cookies.get("admin_activation_ok") or "").strip()
    if "." not in raw:
        return False
    exp, signature = raw.split(".", 1)
    if not exp.isdigit():
        return False
    secret = _get_admin_cookie_secret()
    if not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), exp.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    return int(exp) >= int(time.time())


def _is_admin_mode_request(request: Request) -> bool:
    return request.query_params.get("admin") == "1" and _is_admin_activation_granted(request)


# REMOVIDO: /login alias - não mais necessário



@router.get("/admin-password", response_class=HTMLResponse)
def admin_password_page(request: Request):
    erro = request.query_params.get("erro")
    return templates.TemplateResponse("admin-password.html", {"request": request, "erro": erro})

@router.post("/admin-password")
def admin_password_post(password: str = Form(...)):
    if not _verify_admin_activation_password(password):
        # Redirect with error
        from urllib.parse import quote_plus
        erro = quote_plus("Senha inválida.")
        return RedirectResponse(url=f"/admin-password?erro={erro}", status_code=303)

    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(
        key="admin_activation_ok",
        value=_build_admin_activation_cookie(max_age_seconds=3600),  # 1h
        max_age=3600,
        httponly=True,
        samesite="lax",
    )
    return response


# REMOVIDO: login_submit - não mais necessário


@router.get("/index", response_class=HTMLResponse)
def index(request: Request):
    project_token = _extract_project_token(request)
    return templates.TemplateResponse(
        "Index.html",
        {"request": request, "project_token": project_token}
    )

# REMOVIDO: cadastro routes - não mais necessário


@router.get("/termo", response_class=HTMLResponse)
def termo(request: Request):
    return templates.TemplateResponse(
        "TermoAceite.html",
        {
            "request": request, 
            "project_token": _extract_project_token(request),
            "is_admin": _is_admin_activation_granted(request)
        }
    )


@router.get("/ressalvas", response_class=HTMLResponse)
def ressalvas(request: Request):
    return templates.TemplateResponse(
        "Ressalvas.html",
        {
            "request": request, 
            "project_token": _extract_project_token(request),
            "is_admin": _is_admin_activation_granted(request)
        }
    )


@router.get("/nps", response_class=HTMLResponse)
def nps(request: Request):
    return templates.TemplateResponse(
        "NPS2System.html",
        {"request": request, "project_token": _extract_project_token(request),
         "is_admin": _is_admin_activation_granted(request)}
    )


@router.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    if not _is_admin_activation_granted(request):
        return RedirectResponse(url="/admin-password", status_code=303)

    processos = []

    try:
        res = (
            supabase
            .table("processos")
            .select(
                "codigo,project_token,project_token_ativo,project_token_expira_em,"
                "nome_cliente,empresa,cpf,status,status_entrega,"
                "criado_em,atualizado_em,termo_pdf,pdf_ressalvas,pdf_final,nps_nota,nps_dados"
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
                "termo_pdf,pdf_ressalvas,pdf_final,nps_dados"
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
            p.setdefault("nps_dados", {})

    for p in processos:
        nps_dados = p.get("nps_dados")
        if isinstance(nps_dados, str):
            try:
                nps_dados = json.loads(nps_dados)
            except Exception:
                nps_dados = {}
        if not isinstance(nps_dados, dict):
            nps_dados = {}
        p["nps_dados"] = nps_dados
        # Garante que o link de acesso seja sempre para o index com o token
        p["link_acesso"] = f"{str(request.base_url).rstrip('/')}/index?project_token={p.get('project_token')}"
        p["edicao_permitida"] = not bool(
            nps_dados.get("_edicao_bloqueada")
            or nps_dados.get("_lock_termo")
            or nps_dados.get("_lock_ressalvas")
            or nps_dados.get("_lock_nps")
        )
        criado_em_raw = p.get("criado_em")
        created_text = str(criado_em_raw or "").strip()
        match_dt = re.match(r"^(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2})", created_text)
        if match_dt:
            yyyy, mm, dd, hh, mi = match_dt.groups()
            p["criado_em_fmt"] = f"{dd}/{mm}/{yyyy} {hh}:{mi}"
        else:
            match_d = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", created_text)
            if match_d:
                yyyy, mm, dd = match_d.groups()
                p["criado_em_fmt"] = f"{dd}/{mm}/{yyyy}"
            else:
                p["criado_em_fmt"] = created_text or "-"

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
    if not _is_admin_activation_granted(request):
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador")


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

    link = str(request.base_url).rstrip("/") + f"/index?project_token={token}"
    return JSONResponse({"success": True, "project_token": token, "link": link})



@router.post("/admin/expirar-token")
def admin_expirar_token(
    request: Request,
    project_token: str = Form(...),
):
    if not _is_admin_activation_granted(request):
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador")


    token = (project_token or "").strip().upper()
    if not token:
        raise HTTPException(status_code=400, detail="Project token obrigatorio")

    now_iso = datetime.utcnow().isoformat()
    res = (
        supabase
        .table("processos")
        .update({
            "project_token_expira_em": now_iso,
            "project_token_ativo": False,
        })
        .eq("project_token", token)
        .execute()
    )

    if hasattr(res, "error") and res.error:
        raise HTTPException(status_code=500, detail=res.error.message)

    updated = res.data or []
    if not updated:
        raise HTTPException(status_code=404, detail="Processo nao encontrado para o token informado")

    return JSONResponse(
        {
            "success": True,
            "project_token": token,
            "project_token_expira_em": now_iso,
        }
    )


# REMOVED: alterar-senha-acesso (não mais necessário)


# REMOVIDO: alterar senha admin - não mais necessário


@router.post("/admin/liberar-edicao")
def admin_liberar_edicao(
    request: Request,
    project_token: str = Form(...),
):
    if not _is_admin_activation_granted(request):
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador")


    token = (project_token or "").strip().upper()
    if not token:
        raise HTTPException(status_code=400, detail="Project token obrigatorio")

    proc = (
        supabase
        .table("processos")
        .select("id,nps_dados")
        .eq("project_token", token)
        .limit(1)
        .execute()
    )
    rows = proc.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Processo nao encontrado para o token informado")

    row = rows[0]
    nps_dados = row.get("nps_dados")
    if isinstance(nps_dados, str):
        try:
            nps_dados = json.loads(nps_dados)
        except Exception:
            nps_dados = {}
    if not isinstance(nps_dados, dict):
        nps_dados = {}

    # Logica simplificada: se Termo ou Ressalvas estao travados, consideramos bloqueado (estado de envio pro cliente)
    edit_locked = bool(nps_dados.get("_lock_termo") or nps_dados.get("_lock_ressalvas"))
    now_iso = datetime.utcnow().isoformat()
    actor = "Administrador"

    if edit_locked:
        nps_dados["_lock_termo"] = False
        nps_dados["_lock_ressalvas"] = False
        nps_dados["_lock_nps"] = False
        nps_dados["_edicao_liberada_em"] = now_iso
        nps_dados["_edicao_liberada_por"] = actor
        edicao_permitida = True
    else:
        # Bloqueia Termo/Ressalvas (para admin nao mexer sem querer) e Libera NPS (para cliente preencher)
        nps_dados["_lock_termo"] = True
        nps_dados["_lock_ressalvas"] = True
        nps_dados["_lock_nps"] = False
        nps_dados["_edicao_fechada_em"] = now_iso
        nps_dados["_edicao_fechada_por"] = actor
        edicao_permitida = False

    upd = (
        supabase
        .table("processos")
        .update({"nps_dados": nps_dados, "atualizado_em": now_iso})
        .eq("id", row.get("id"))
        .execute()
    )
    if hasattr(upd, "error") and upd.error:
        raise HTTPException(status_code=500, detail=upd.error.message)

    return JSONResponse({"success": True, "project_token": token, "edicao_permitida": edicao_permitida})

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
        url="/admin-password",
        status_code=status.HTTP_303_SEE_OTHER
    )
    # Remove o cookie de autenticação para encerrar a sessão
    response.delete_cookie("nps_user")
    response.delete_cookie("nps_role")
    response.delete_cookie("project_token")
    response.delete_cookie("nps_tipo_acesso")
    response.delete_cookie("admin_activation_ok")
    return response
