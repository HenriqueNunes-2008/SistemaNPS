import base64
import mimetypes
import os
import uuid

from app.services.supabase_client import supabase


def upload_pdf(data_or_path: str, folder_or_path: str) -> str:
    """
    Recebe base64 (data:...;base64,...) ou caminho de arquivo local.
    Faz upload no Supabase Storage (bucket: processos).
    Retorna URL publica.
    """
    try:
        # ---------------------------------
        # 1. Bytes e content-type
        # ---------------------------------
        content_type = None
        file_bytes = None

        if os.path.isfile(data_or_path):
            with open(data_or_path, "rb") as f:
                file_bytes = f.read()
            content_type, _ = mimetypes.guess_type(data_or_path)
        else:
            if "," in data_or_path and data_or_path.strip().lower().startswith("data:"):
                header, b64 = data_or_path.split(",", 1)
                if ";" in header:
                    content_type = header.split(":", 1)[1].split(";", 1)[0]
                file_bytes = base64.b64decode(b64)
            else:
                file_bytes = base64.b64decode(data_or_path)

        if not file_bytes:
            raise Exception("Arquivo vazio ou invalido")

        if not content_type:
            content_type = "application/pdf"

        # ---------------------------------
        # 2. Path remoto
        # ---------------------------------
        if folder_or_path.lower().endswith((".pdf", ".png", ".jpg", ".jpeg")):
            path = folder_or_path
        else:
            ext = ".pdf"
            if content_type == "image/png":
                ext = ".png"
            elif content_type == "image/jpeg":
                ext = ".jpg"
            filename = f"{uuid.uuid4()}{ext}"
            path = f"{folder_or_path}/{filename}"

        # ---------------------------------
        # 3. Upload
        # ---------------------------------
        res = supabase.storage.from_("processos").upload(
            path,
            file_bytes,
            file_options={
                "content-type": content_type,
                "upsert": False
            }
        )

        if hasattr(res, "error") and res.error:
            raise Exception(res.error.message)
        if isinstance(res, dict) and res.get("error"):
            raise Exception(res.get("error"))

        # ---------------------------------
        # 4. URL publica
        # ---------------------------------
        public_url = supabase.storage.from_("processos").get_public_url(path)

        return public_url

    except Exception as e:
        raise Exception(f"Falha no upload: {str(e)}")
