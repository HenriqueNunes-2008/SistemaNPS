from fastapi import APIRouter
from app.schemas import RespostaCreate
from app.services.supabase_client import supabase

router = APIRouter(prefix="/api")

@router.post("/respostas")
def salvar_resposta(resposta: RespostaCreate):
    supabase.table("respostas").insert({
        "cliente_id": resposta.cliente_id,
        "pagina": resposta.pagina,
        "dados": resposta.dados
    }).execute()
    return {"status": "ok"}
