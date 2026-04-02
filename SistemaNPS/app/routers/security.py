import hmac
import hashlib
import time
import os
from fastapi import Request
from datetime import datetime, timezone

def get_admin_cookie_secret() -> str:
    """Recupera a chave secreta para assinatura de cookies administrativos."""
    return (
        os.getenv("ADMIN_ACTIVATION_COOKIE_SECRET")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or ""
    )

def is_admin_activation_granted(request: Request) -> bool:
    """Verifica se o cookie de administrador e valido e nao expirou."""
    raw = (request.cookies.get("admin_activation_ok") or "").strip()
    if "." not in raw:
        return False
    
    try:
        exp, signature = raw.split(".", 1)
        if not exp.isdigit():
            return False
        
        secret = get_admin_cookie_secret()
        if not secret:
            return False
            
        expected = hmac.new(secret.encode("utf-8"), exp.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
            
        return int(exp) >= int(time.time())
    except Exception:
        return False

def is_admin_mode_request(request: Request) -> bool:
    """Verifica se a requisicao possui permissao de admin e se o modo admin foi solicitado."""
    is_granted = is_admin_activation_granted(request)
    has_admin_param = (
        request.query_params.get("admin") == "1" 
        or request.query_params.get("return") == "/admin"
    )
    return is_granted and has_admin_param
