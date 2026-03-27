from fastapi import FastAPI, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from urllib.parse import quote_plus

# Imports diretos dos routers
from app.routers.public import router as public_router
from app.routers.nps import router as nps_router
from app.routers.termo import router as termo_router
from app.routers.ressalvas import router as ressalvas_router

app = FastAPI(title="Sistema NPS Simplificado")

# Static
app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

# Routers essenciais
app.include_router(public_router)
app.include_router(nps_router)
app.include_router(termo_router)
app.include_router(ressalvas_router)

# Admin password page
@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    erro = request.query_params.get("erro")
    return templates.TemplateResponse(
        request=request,
        name="admin-password.html",
        context={"erro": erro}
    )

@app.post("/admin-password")
def admin_password_post(request: Request, password: str = Form(...)):
    from app.routers.public import _verify_admin_activation_password, _build_admin_activation_cookie
    if not _verify_admin_activation_password(password):
        erro = quote_plus("Senha inválida.")
        return RedirectResponse(url=f"/?erro={erro}", status_code=303)

    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(
        key="admin_activation_ok",
        value=_build_admin_activation_cookie(3600),
        max_age=3600,
        httponly=True,
        samesite="lax"
    )
    return response

print("Sistema NPS pronto! Acesse http://localhost:8000")
