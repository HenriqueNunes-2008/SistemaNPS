import uuid
from typing import Optional, Dict, Any, List
from app.services.supabase_client import supabase

class ProcessoRepository:
    """Centraliza todas as operacoes de banco de dados para a tabela processos."""
    
    @staticmethod
    def get_by_identifier(identifier: str, select: str = "*") -> Optional[Dict[str, Any]]:
        """Busca um processo por id, project_token ou codigo."""
        # Ignora identificadores vazios ou a string literal "None" vinda de links mal formados
        if not identifier or str(identifier).lower() == "none":
            return None
            
        search_fields = ["project_token", "codigo"]
        try:
            # Só tenta buscar por 'id' se o identificador tiver formato de UUID
            uuid.UUID(str(identifier))
            search_fields.insert(0, "id")
        except (ValueError, TypeError):
            pass

        for field in search_fields:
            res = supabase.table("processos").select(select).eq(field, identifier).limit(1).execute()
            if res.data:
                return res.data[0]
        return None

    @staticmethod
    def insert(data: Dict[str, Any]) -> Dict[str, Any]:
        """Insere um novo processo."""
        res = supabase.table("processos").insert(data).execute()
        if hasattr(res, "error") and res.error:
            raise Exception(f"Erro ao inserir processo: {res.error.message}")
        return res.data[0]

    @staticmethod
    def update(processo_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Atualiza um processo existente pelo UUID."""
        res = supabase.table("processos").update(data).eq("id", processo_id).execute()
        if hasattr(res, "error") and res.error:
            raise Exception(f"Erro ao atualizar processo: {res.error.message}")
        if not res.data:
            raise Exception(f"Nenhum registro encontrado para atualizar (ID: {processo_id})")
        return res.data[0]

    @staticmethod
    def insert_ressalvas_itens(itens: List[Dict[str, Any]]):
        """Insere itens na tabela de ressalvas."""
        return supabase.table("ressalvas_itens").insert(itens).execute()

    @staticmethod
    def delete_ressalvas_itens(processo_uuid: str):
        """Remove itens de ressalva de um processo."""
        return supabase.table("ressalvas_itens").delete().eq("processo_id", processo_uuid).execute()

    @staticmethod
    def get_ressalvas_itens(processo_uuid: str) -> List[Dict[str, Any]]:
        """Busca itens de ressalva para um processo."""
        res = supabase.table("ressalvas_itens").select("*").eq("processo_id", processo_uuid).execute()
        return res.data or []