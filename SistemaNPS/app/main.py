from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
import os

from app.routers import public, nps
from app.routers.termo import router as termo_router
from app.routers.ressalvas import router as ressalvas_router
from app.services import upload

app = FastAPI(title="Sistema NPS Simplificado")

# Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

# Health Check para o Render
@app.get("/health")
async def health():
    return {"status": "ok"}

# Mount routers (reuse existing, simplificados depois)
app.include_router(public.router)
app.include_router(nps.router)

app.include_router(termo_router, prefix="/termo")
app.include_router(ressalvas_router, prefix="/ressalvas")

# Root redirect to admin-password
@app.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse(url="/admin-password", status_code=303)

# Simple admin-password page
@app.get("/admin-password", response_class=HTMLResponse)
async def admin_password():
    return templates.TemplateResponse("admin-password.html", {"request": {}})

@app.post("/admin-password")
async def post_admin_password():
    # Will be handled in public.py simplificado
    return RedirectResponse(url="/admin", status_code=303)

print("Sistema NPS Simplificado iniciado - /admin-password para acessar admin")
