from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import quote_plus

router = APIRouter(prefix="/admin", tags=["Admin"])
templates = Jinja2Templates(directory="app/templates")

# Import shared functions from public
from .public import _is_admin_activation_granted, _verify_admin_activation_password, _build_admin_activation_cookie

@router.get("/password", response_class=HTMLResponse)
def admin_password_get(request: Request):
    erro = request.query_params.get("erro")
    return templates.TemplateResponse(
        request=request,
        name="admin-password.html",
        context={"request": request, "erro": erro}
    )

@router.post("/password")
def admin_password_post(password: str = Form(...)):
    if not _verify_admin_activation_password(password):
        erro = quote_plus("Senha inválida.")
        return RedirectResponse(url=f"/admin/password?erro={erro}", status_code=303)
    
    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(
        key="admin_activation_ok",
        value=_build_admin_activation_cookie(max_age_seconds=3600),
        max_age=3600,
        httponly=True,
        samesite="lax",
    )
    return response
