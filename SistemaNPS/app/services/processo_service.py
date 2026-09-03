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
from app.services.termos import salvar_termo_extra
from app.services.shared_data import as_dict
from app.services.supabase_client import supabase

class ProcessoService:
    """Orquestra a logica de negocio dos processos NPS."""

    ETAPAS_CLIENTE = ("aceite", "ressalvas", "recebimento", "treinamento", "assinatura", "nps")

    @staticmethod
    def _normalizar_prazo(value: Any) -> str | None:
        """Converte prazos vindos do banco ou do frontend para ISO sem perder nulos."""
        if value is None or value == "":
            return None
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
            except ValueError as exc:
                try:
                    return date.fromisoformat(value).isoformat()
                except ValueError:
                    raise ValueError("Prazo inválido; use uma data ISO.") from exc
        raise TypeError("Prazo deve ser date, datetime, string ISO ou nulo.")

    @classmethod
    def etapa_atual_cliente(cls, processo: dict | None) -> str | None:
        """Obtém a etapa pública sem alterar processos antigos."""
        if not processo:
            return None
        nps = as_dict(processo.get("nps_dados"))
        etapa = nps.get("_etapa_fluxo")
        if etapa in cls.ETAPAS_CLIENTE:
            return etapa
        if processo.get("assinatura_cliente_url"):
            return "nps"
        if nps.get("_lock_termo") or nps.get("_lock_ressalvas"):
            return "aceite"
        return None

    @classmethod
    def proxima_etapa_cliente(cls, processo: dict, etapa: str) -> str:
        atual = cls.etapa_atual_cliente(processo)
        if etapa not in cls.ETAPAS_CLIENTE or atual != etapa:
            raise ValueError("Esta etapa ainda não está liberada para conclusão.")
        indice = cls.ETAPAS_CLIENTE.index(etapa)
        if indice >= len(cls.ETAPAS_CLIENTE) - 1:
            return etapa
        proxima = cls.ETAPAS_CLIENTE[indice + 1]
        nps = as_dict(processo.get("nps_dados"))
        nps["_etapa_fluxo"] = proxima
        nps["_etapa_fluxo_atualizada_em"] = datetime.utcnow().isoformat()
        ProcessoRepository.update(processo["id"], {"nps_dados": nps, "atualizado_em": datetime.utcnow().isoformat()})
        return proxima

    @classmethod
    def documentos_pendentes_para_etapa(cls, processo: dict, etapa: str) -> list[str]:
        """Retorna documentos que precisam existir antes de concluir uma etapa."""
        campos = {
            "aceite": (("termo_dados", "termo_pdf"), "Termo de Aceite"),
            "ressalvas": (("ressalvas_dados", "pdf_ressalvas"), "Ressalvas"),
            "recebimento": (("recebimento_dados", "recebimento_pdf"), "Termo de Recebimento"),
            "treinamento": (("treinamento_dados", "treinamento_pdf"), "Termo de Treinamento"),
        }
        pendentes = []
        campos_documento, nome = campos.get(etapa, ((), None))
        if campos_documento and (
            not as_dict(processo.get(campos_documento[0]))
            or not processo.get(campos_documento[1])
        ):
            pendentes.append(nome)

        return pendentes

    @classmethod
    def documentos_obrigatorios_pendentes(cls, processo: dict) -> list[str]:
        """Valida os quatro documentos exigidos para a assinatura."""
        pendentes = cls.documentos_pendentes_para_etapa(processo, "aceite")
        status = str(processo.get("status_entrega") or "").strip().lower()
        if status != "concluido" and not as_dict(processo.get("ressalvas_dados")):
            pendentes.append("Ressalvas")
        pendentes.extend(cls.documentos_pendentes_para_etapa(processo, "recebimento"))
        pendentes.extend(cls.documentos_pendentes_para_etapa(processo, "treinamento"))
        return pendentes

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
            # Dados antigos podem trazer somente a URL da imagem.
            if hasattr(item, "dict"):
                item_dict = item.dict()
            elif isinstance(item, dict):
                item_dict = dict(item)
            elif isinstance(item, str):
                item_dict = {"item": idx + 1, "imagem_base64": item}
            else:
                continue
            item_key = str(item_dict.get("item") or (idx + 1))
            
            img_b64 = item_dict.get("imagem_base64")
            # Se nao enviou nova imagem, tenta recuperar a existente (URL do storage)
            if not img_b64 and item_key in mapa_existente:
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
        if existing_proc:
            existing_termo = as_dict(existing_proc.get("termo_dados"))
            for key in ("assinatura_cliente_path", "assinatura_cliente_url"):
                if existing_termo.get(key) and not termo_dados.get(key):
                    termo_dados[key] = existing_termo[key]
        folder = f"{processo_uuid}/termo"

        # Persiste as imagens antes do PDF para que o gerador trabalhe com as
        # mesmas referências que ficam armazenadas no processo.
        for img in termo_dados["itens"]:
            img_src = img.get("imagem_base64")
            if img_src and str(img_src).startswith("data:"):
                img["imagem_base64"] = upload_pdf(img_src, folder)

        data.termo_dados = termo_dados

        # 1. Gerar PDF
        buffer = gerar_pdf_termo_buffer(data)
        pdf_base64 = "data:application/pdf;base64," + base64.b64encode(buffer.read()).decode()

        # 2. Upload do PDF
        termo_url = upload_pdf(pdf_base64, folder)

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
        status_entrega = str(data.status_entrega or "").strip().lower()
        if status_entrega == "concluido_com_ressalva":
            etapa_fluxo = "ressalvas"
        elif status_entrega == "concluido":
            etapa_fluxo = "recebimento"
        else:
            etapa_fluxo = "aceite"
        nps_dados = as_dict(existing_proc.get("nps_dados")) if existing_proc else {}
        nps_dados["_etapa_fluxo"] = etapa_fluxo
        nps_dados["_etapa_fluxo_atualizada_em"] = datetime.utcnow().isoformat()
        payload["nps_dados"] = nps_dados

        if is_update:
            payload["atualizado_em"] = datetime.utcnow().isoformat()
            result = ProcessoRepository.update(processo_uuid, payload)
        else:
            payload["id"] = processo_uuid
            payload["codigo"] = cls.gerar_codigo_humano(data.nome_cliente, data.cpf)
            payload["status"] = "TERMO_GERADO"
            payload["criado_em"] = datetime.utcnow().isoformat()
            result = ProcessoRepository.insert(payload)

        if status_entrega in ("concluido", "concluido_com_ressalva"):
            result = cls.sincronizar_termos_derivados(result)
            if result.get("codigo"):
                regenerate_final_pdf_by_codigo(result["codigo"], set_status_finalizado=False)
        return result

    @classmethod
    def sincronizar_termos_derivados(cls, processo: dict) -> dict:
        """Gera e persiste os termos derivados com os dados atuais do Aceite."""
        if not processo or not processo.get("id"):
            raise ValueError("Processo inválido para sincronização dos termos derivados.")

        resultado = processo
        for tipo in ("recebimento", "treinamento"):
            try:
                resultado = salvar_termo_extra(resultado, tipo)
            except Exception as exc:
                raise RuntimeError(
                    f"Não foi possível gerar o Termo de {tipo.capitalize()}: {exc}"
                ) from exc
            resultado = resultado.get("processo") or resultado
        return resultado

    @staticmethod
    def salvar_ressalvas_fluxo(data: Any, is_update: bool = False, existing_proc: dict = None) -> Tuple[str, str]:
        """Executa todo o fluxo de salvar/atualizar ressalvas: PDF -> Upload -> DB."""
        processo_uuid = existing_proc["id"]
        processo_codigo = existing_proc.get("codigo") or data.processo_id

        # 1. Gerar PDF
        pdf_buffer = gerar_pdf_ressalvas_buffer(
            data.responsavel, data.cpf, data.imagens,
            (existing_proc or {}).get("assinatura_cliente_url"),
        )
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
                "prazo": ProcessoService._normalizar_prazo(img.prazo),
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
                    "prazo": ProcessoService._normalizar_prazo(img.prazo),
                    "responsavel": img.responsavel,
                    "observacao": img.observacao,
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
        nps_dados = as_dict(existing_proc.get("nps_dados"))
        etapa_atual = ProcessoService.etapa_atual_cliente(existing_proc)
        etapa_fluxo = (
            etapa_atual
            if etapa_atual in ProcessoService.ETAPAS_CLIENTE
            and ProcessoService.ETAPAS_CLIENTE.index(etapa_atual) > ProcessoService.ETAPAS_CLIENTE.index("recebimento")
            else "recebimento"
        )
        nps_dados["_etapa_fluxo"] = etapa_fluxo
        nps_dados["_etapa_fluxo_atualizada_em"] = datetime.utcnow().isoformat()
        payload_update["nps_dados"] = nps_dados
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

    @staticmethod
    def dados_compartilhados(processo: dict) -> dict:
        from app.services.shared_data import dados_compartilhados
        return dados_compartilhados(processo)

    @staticmethod
    def salvar_recebimento_fluxo(existing_proc: dict, dados: dict | None = None):
        return salvar_termo_extra(existing_proc, "recebimento", dados)

    @staticmethod
    def salvar_treinamento_fluxo(existing_proc: dict, dados: dict | None = None):
        return salvar_termo_extra(existing_proc, "treinamento", dados)

    @staticmethod
    def salvar_assinatura_cliente_fluxo(processo: dict, caminho: str):
        termo = dict(as_dict(processo.get("termo_dados")))
        termo["assinatura_cliente_path"] = caminho
        termo["assinatura_cliente_url"] = caminho
        nps = as_dict(processo.get("nps_dados"))
        nps["_etapa_fluxo"] = "nps"
        nps["_etapa_fluxo_atualizada_em"] = datetime.utcnow().isoformat()
        # A condição no banco elimina a janela de corrida entre duas abas:
        # uma assinatura já gravada nunca é substituída.
        result = supabase.table("processos").update({
            "termo_dados": termo,
            "assinatura_cliente_url": caminho,
            "assinatura_cliente_criada_em": datetime.utcnow().isoformat(),
            "nps_dados": nps,
        }).eq("id", processo["id"]).is_("assinatura_cliente_url", "null").execute()
        if not result.data:
            raise ValueError("Este processo já possui uma assinatura digital.")
        return result.data[0]
