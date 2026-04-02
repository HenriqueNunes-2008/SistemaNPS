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
from app.routers.processos import router as processos_router

app = FastAPI(title="Sistema NPS Simplificado")

# Static
app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

# Routers essenciais
app.include_router(public_router)
app.include_router(nps_router)
app.include_router(termo_router)
app.include_router(ressalvas_router)
app.include_router(processos_router)

# Admin password page
@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    erro = request.query_params.get("erro")
    return templates.TemplateResponse(
        request=request,
        name="admin-password.html",
        context={"request": request, "erro": erro}
    )

@app.post("/admin-password")
def admin_password_post(request: Request, password: str = Form(...)):
    # Como a rota já existe no public_router que foi incluído acima, 
    # vamos deixar que o public.py gerencie o POST para evitar conflitos.
    from app.routers.public import admin_password_post as public_post
    return public_post(password=password)

print("Sistema NPS pronto! Acesse http://localhost:8000")
