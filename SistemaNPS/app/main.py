from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.routers import public, respostas, termo, ressalvas, finalizacao, nps, processos

app = FastAPI(title="Sistema de Termos")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

app.include_router(public.router)
app.include_router(respostas.router)
app.include_router(termo.router)
app.include_router(ressalvas.router)
app.include_router(finalizacao.router)
app.include_router(nps.router)
app.include_router(processos.router)
