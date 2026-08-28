import base64
import uuid
from typing import Optional

from app.services.supabase_client import supabase


BUCKET_ASSINATURAS = "assinaturas"


def salvar_assinatura(
    assinatura_base64: str,
    processo_id: str,
) -> str:
    """
    Recebe a assinatura em Data URL/Base64,
    salva como PNG no Supabase Storage
    e retorna o caminho do arquivo.
    """

    if not assinatura_base64:
        raise ValueError("Assinatura não informada.")

    if not assinatura_base64.startswith("data:image/png;base64,"):
        raise ValueError("Formato de assinatura inválido.")

    try:
        _, encoded = assinatura_base64.split(",", 1)
        imagem = base64.b64decode(encoded)
    except Exception as exc:
        raise ValueError("Não foi possível decodificar a assinatura.") from exc

    if not imagem:
        raise ValueError("A assinatura está vazia.")

    # Limite de segurança: 2 MB
    if len(imagem) > 2 * 1024 * 1024:
        raise ValueError("A assinatura é muito grande.")

    nome_arquivo = f"{uuid.uuid4().hex}.png"
    caminho = f"{processo_id}/{nome_arquivo}"

    try:
        supabase.storage \
            .from_(BUCKET_ASSINATURAS) \
            .upload(
                caminho,
                imagem,
                {
                    "content-type": "image/png",
                    "cache-control": "3600",
                    "upsert": "false",
                },
            )
    except Exception as exc:
        raise RuntimeError(
            f"Erro ao salvar assinatura no Supabase Storage: {exc}"
        ) from exc

    return caminho


def gerar_url_assinatura(
    caminho: str,
    expires_in: int = 3600,
) -> Optional[str]:
    """
    Gera uma URL temporária para um arquivo privado
    do bucket de assinaturas.
    """

    if not caminho:
        return None

    try:
        resultado = supabase.storage \
            .from_(BUCKET_ASSINATURAS) \
            .create_signed_url(
                caminho,
                expires_in,
            )

        if isinstance(resultado, dict):
            return (
                resultado.get("signedURL")
                or resultado.get("signedUrl")
                or resultado.get("signed_url")
            )

        return None

    except Exception:
        return None