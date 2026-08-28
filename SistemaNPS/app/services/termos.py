from __future__ import annotations

from datetime import datetime
from typing import Any
from app.services.processo_repository import ProcessoRepository
from app.services.shared_data import dados_compartilhados, as_dict
from app.services.pdf_service import gerar_pdf_recebimento_buffer, gerar_pdf_treinamento_buffer
from app.services.upload import upload_pdf
import base64


def salvar_termo_extra(processo: dict, tipo: str, dados: dict | None = None) -> dict:
    """Salva o termo extra em JSON e PDF; nunca aceita dados de cliente como fonte."""
    compartilhados = dados_compartilhados(processo)
    payload = dict(compartilhados)
    payload.update(dados or {})
    payload["produto"] = compartilhados["produto"]
    payload["codigo_entrega"] = compartilhados["codigo_entrega"]
    payload["data"] = compartilhados["data"]
    payload["nome_cliente"] = compartilhados["nome_cliente"]
    payload["cpf_cliente"] = compartilhados["cpf_cliente"]
    payload["representante_nome"] = compartilhados["representante_nome"]
    payload["representante_cpf"] = compartilhados["representante_cpf"]
    payload["assinatura_cliente_url"] = compartilhados["assinatura_cliente_url"]
    generator = gerar_pdf_recebimento_buffer if tipo == "recebimento" else gerar_pdf_treinamento_buffer
    pdf = generator(payload)
    url = upload_pdf("data:application/pdf;base64," + base64.b64encode(pdf.read()).decode(),
                     f"{processo['id']}/{tipo}")
    column = f"{tipo}_dados"
    pdf_column = f"{tipo}_pdf"
    update = {column: payload, pdf_column: url, "atualizado_em": datetime.utcnow().isoformat()}
    result = ProcessoRepository.update(processo["id"], update)
    return {"dados": payload, "pdf_url": url, "processo": result}
