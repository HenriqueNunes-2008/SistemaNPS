import re
import random
import string
import uuid
import base64
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, date, timezone

from app.services.processo_repository import ProcessoRepository
from app.services.pdf_service import gerar_pdf_termo_buffer, gerar_pdf_ressalvas_buffer
from app.services.upload import upload_pdf
from app.services.final_pdf import regenerate_final_pdf_by_codigo
from app.routers.utils import gerar_hash_imagem

class ProcessoService:
    """Orquestra a logica de negocio dos processos NPS."""

    @staticmethod
    def gerar_codigo_humano(nome_cliente: str, cpf: str) -> str:
        """Gera o codigo padrão: NOME_CPF3_DATA_RAND."""
        primeiro_nome = re.sub(r"[^A-Z]", "", nome_cliente.split()[0].upper())
        ultimos_cpf = re.sub(r"\D", "", cpf)[-3:]
        data_hoje = datetime.now().strftime("%Y-%m-%d")
        sufixo = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        return f"{primeiro_nome}_{ultimos_cpf}_{data_hoje}_{sufixo}"

    @staticmethod
    def _normalizar_dados_termo(data: Any, existing_imgs: List[Dict] = None) -> Dict[str, Any]:
        """Garante que as imagens e campos JSON estejam no formato correto antes de salvar."""
        termo_dados = data.termo_dados or {}
        
        # Reconstrucao de campos se vierem na raiz do payload (compatibilidade frontend)
        if data.campos and not termo_dados.get("campos"):
            termo_dados["campos"] = data.campos
        if data.assinaturas and not termo_dados.get("assinaturas"):
            termo_dados["assinaturas"] = data.assinaturas
        if data.data and not termo_dados.get("data"):
            termo_dados["data"] = data.data

        # Normalizacao de Imagens
        # Se não vierem novas imagens, tenta manter as existentes para não perder no PDF
        itens_input = data.imagens or termo_dados.get("itens") or existing_imgs or []
        mapa_existente = {str(img.get("item")): img for img in (existing_imgs or []) if isinstance(img, dict)}
        
        itens_finais = []
        for idx, item in enumerate(itens_input):
            item_dict = item.dict() if hasattr(item, "dict") else dict(item)
            item_key = str(item_dict.get("item") or (idx + 1))
            
            img_b64 = item_dict.get("imagem_base64")
            # Se nao enviou nova imagem, tenta recuperar a existente (URL do storage)
            if (not img_b64 or "," not in img_b64) and item_key in mapa_existente:
                item_dict["imagem_base64"] = mapa_existente[item_key].get("imagem_base64")
                item_dict["imagem_hash"] = mapa_existente[item_key].get("imagem_hash")
            elif img_b64 and "," in img_b64:
                item_dict["imagem_hash"] = gerar_hash_imagem(img_b64)
            
            itens_finais.append(item_dict)
        
        termo_dados["itens"] = itens_finais
        return termo_dados

    @classmethod
    def salvar_termo_fluxo(cls, data: Any, is_update: bool = False, existing_proc: dict = None):
        """Executa todo o fluxo de salvar/atualizar termo: PDF -> Upload -> DB."""
        processo_uuid = existing_proc["id"] if existing_proc else str(uuid.uuid4())
        project_token = existing_proc["project_token"] if existing_proc else uuid.uuid4().hex[:12].upper()
        
        # 0. Normalizacao
        existing_imgs = existing_proc.get("imagens_termo") if existing_proc else []
        termo_dados = cls._normalizar_dados_termo(data, existing_imgs)
        data.termo_dados = termo_dados # Atualiza o objeto para o gerador de PDF

        # 1. Gerar PDF
        buffer = gerar_pdf_termo_buffer(data)
        pdf_base64 = "data:application/pdf;base64," + base64.b64encode(buffer.read()).decode()
        
        # 2. Upload
        folder = f"{processo_uuid}/termo"
        termo_url = upload_pdf(pdf_base64, folder)
        
        # Upload de imagens de itens (opcional/background)
        for img in termo_dados["itens"]:
            if img.get("imagem_base64") and "," in img["imagem_base64"]:
                try: 
                    # Persiste a URL pública de volta no campo da imagem para salvar no banco
                    img_url = upload_pdf(img["imagem_base64"], folder)
                    img["imagem_base64"] = img_url
                except: pass

        # 3. Preparar Payload
        cpf_limpo = re.sub(r"\D", "", data.cpf)
        payload = {
            "project_token": project_token,
            "nome_cliente": data.nome_cliente,
            "empresa": data.empresa,
            "cpf": cpf_limpo,
            "status_entrega": data.status_entrega,
            "termo_pdf": termo_url,
            "termo_dados": termo_dados,
            "imagens_termo": termo_dados["itens"]
        }

        if is_update:
            payload["atualizado_em"] = datetime.utcnow().isoformat()
            result = ProcessoRepository.update(processo_uuid, payload)
            regenerate_final_pdf_by_codigo(result["codigo"], set_status_finalizado=False)
        else:
            payload["id"] = processo_uuid
            payload["codigo"] = cls.gerar_codigo_humano(data.nome_cliente, data.cpf)
            payload["status"] = "TERMO_GERADO"
            payload["criado_em"] = datetime.utcnow().isoformat()
            result = ProcessoRepository.insert(payload)
        
        return result

    @staticmethod
    def salvar_ressalvas_fluxo(data: Any, is_update: bool = False, existing_proc: dict = None) -> Tuple[str, str]:
        """Executa todo o fluxo de salvar/atualizar ressalvas: PDF -> Upload -> DB."""
        processo_uuid = existing_proc["id"]
        processo_codigo = existing_proc.get("codigo") or data.processo_id

        # 1. Gerar PDF
        pdf_buffer = gerar_pdf_ressalvas_buffer(data.responsavel, data.cpf, data.imagens)
        pdf_base64 = "data:application/pdf;base64," + base64.b64encode(pdf_buffer.read()).decode()

        # 2. Upload
        folder = f"{processo_uuid}/ressalvas"
        pdf_url = upload_pdf(pdf_base64, folder)
        if not pdf_url:
            raise Exception("Falha no upload do PDF de ressalvas")

        # Upload das imagens individuais para o Storage
        for img in data.imagens:
            if img.imagem_base64 and "," in img.imagem_base64:
                try: upload_pdf(img.imagem_base64, folder)
                except: pass

        # 3. Preparar e Inserir Itens de Ressalvas
        itens_payload = []
        for img in data.imagens:
            itens_payload.append({
                "processo_id": processo_uuid,
                "item": img.item,
                "descricao": img.descricao,
                "prazo": img.prazo.isoformat() if img.prazo else None,
                "aprovacao": img.aprovacao,
                "imagem_hash": (
                    gerar_hash_imagem(img.imagem_base64)
                    if img.imagem_base64 and "," in img.imagem_base64 else None
                ),
                "criado_em": datetime.utcnow().isoformat()
            })
        
        if is_update:
            ProcessoRepository.delete_ressalvas_itens(processo_uuid)
        if itens_payload:
            ProcessoRepository.insert_ressalvas_itens(itens_payload)

        # 4. Atualizar Processo Principal
        ressalvas_dados = {
            "responsavel": data.responsavel,
            "cpf": data.cpf,
            "observacoes": data.observacoes,
            "itens": [
                {
                    "item": img.item,
                    "descricao": img.descricao,
                    "prazo": img.prazo.isoformat() if img.prazo else None,
                    "responsavel": img.responsavel,
                    "observacao": img.observacao,
                    "aprovacao": img.aprovacao,
                    "imagem_base64": img.imagem_base64
                }
                for img in data.imagens
            ]
        }

        payload_update = {
            "status": "RESSALVAS_REGISTRADAS",
            "pdf_ressalvas": pdf_url,
            "ressalvas_dados": ressalvas_dados,
            "atualizado_em": datetime.utcnow().isoformat()
        }
        ProcessoRepository.update(processo_uuid, payload_update)

        regenerate_final_pdf_by_codigo(processo_codigo, set_status_finalizado=False)
        return pdf_url, processo_codigo

    @staticmethod
    def finalizar_nps_fluxo(processo_id: str, nps_nota: int, nps_dados: dict, flow_type: str, proc_codigo: str, is_update: bool = False) -> str:
        """Processa a finalizacao do NPS e gera o PDF consolidado."""
        nps_dados.update({
            "nps": nps_nota,
            "avaliacoes": nps_dados.get("avaliacoes", {}),
            "feedback": nps_dados.get("feedback", {}),
        })
        nps_dados["_lock_nps"] = True
        nps_dados["_lock_nps_por"] = flow_type

        update_data = {
            "nps_dados": nps_dados,
            "nps_nota": nps_nota,
        }
        if is_update:
            update_data["atualizado_em"] = datetime.utcnow().isoformat()
        else:
            update_data["finalizado_em"] = date.today().isoformat()

        ProcessoRepository.update(processo_id, update_data)

        final_url = regenerate_final_pdf_by_codigo(proc_codigo, set_status_finalizado=not is_update)
        if not final_url:
            raise Exception("Não foi possível gerar PDF final sem dados completos")
        return final_url

    @staticmethod
    def is_token_expired(proc: dict | None) -> bool:
        """Valida se o token do processo ainda esta ativo e dentro do prazo de validade."""
        if not proc or proc.get("project_token_ativo") is False:
            return True
        expires = proc.get("project_token_expira_em")
        if not expires: return False
        try:
            exp_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            if exp_dt.tzinfo is None: exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            return exp_dt < datetime.now(timezone.utc)
        except Exception:
            return False
