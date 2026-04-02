import base64
import hashlib
import json
from typing import Any

def normalize_base64(encoded: str) -> str:
    """Remove quebras de linha e espacos de uma string base64 e garante o padding correto."""
    encoded = encoded.strip().replace("\n", "").replace("\r", "").replace(" ", "")
    missing_padding = len(encoded) % 4
    if missing_padding:
        encoded += "=" * (4 - missing_padding)
    return encoded

def gerar_hash_imagem(base64_data: str) -> str:
    """Extrai o conteudo de um data URI base64 e gera um hash SHA256."""
    if "," in base64_data:
        _, encoded = base64_data.split(",", 1)
    else:
        encoded = base64_data
    
    encoded = normalize_base64(encoded)
    raw = base64.b64decode(encoded)
    return hashlib.sha256(raw).hexdigest()

def parse_json_object(value: Any) -> dict:
    """Converte strings JSON em dicionarios de forma segura, retornando um dict vazio em caso de erro."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}

def parse_json_list(value: Any) -> list:
    """Converte strings JSON em listas de forma segura, retornando uma lista vazia em caso de erro."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    return []
