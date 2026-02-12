from pydantic import BaseModel
from typing import Dict

class RespostaCreate(BaseModel):
    cliente_id: str
    pagina: str
    dados: Dict
