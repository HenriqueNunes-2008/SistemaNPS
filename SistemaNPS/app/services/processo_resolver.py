from __future__ import annotations

from fastapi import HTTPException

from app.services.supabase_client import supabase


def _query_processo(identifier: str, select_fields: str):
    normalized = (identifier or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Identificador de processo ausente")

    # project_token e o identificador principal de fluxo.
    for field in ("project_token", "codigo"):
        query = (
            supabase
            .table("processos")
            .select(select_fields)
            .eq(field, normalized)
            .limit(1)
            .execute()
        )
        rows = query.data or []
        if rows:
            return rows[0]

    raise HTTPException(status_code=404, detail="Processo nao encontrado")


def obter_processo_por_identificador(identifier: str, select_fields: str):
    return _query_processo(identifier, select_fields)

