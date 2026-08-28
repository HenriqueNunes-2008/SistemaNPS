"""Dados comuns aos termos, derivados exclusivamente do processo salvo."""
from __future__ import annotations

import json
from typing import Any


def as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def dados_compartilhados(processo: dict) -> dict:
    termo = as_dict(processo.get("termo_dados"))
    campos = as_dict(termo.get("campos"))
    assinaturas = as_dict(termo.get("assinaturas"))
    data = as_dict(termo.get("data"))
    produto_codigo = (
        campos.get("PRODUTO E CÓDIGO DA ENTREGA")
        or campos.get("Produto e Código da Entrega")
        or campos.get("produto_codigo_entrega")
        or termo.get("produto_codigo_entrega")
        or ""
    )
    produto = (campos.get("Produto") or campos.get("produto") or
               campos.get("Produto/Equipamento") or termo.get("produto") or "")
    codigo = termo.get("codigo_entrega") or ""
    if produto_codigo:
        produto = str(produto_codigo)
        codigo = ""
    representante = assinaturas.get("representante") or {}
    assinatura = (processo.get("assinatura_cliente_url") or
                  termo.get("assinatura_cliente_url") or
                  termo.get("assinatura_cliente_path"))
    return {
        "produto": str(produto),
        "codigo_entrega": str(codigo),
        "data": data,
        "nome_cliente": processo.get("nome_cliente") or "",
        "cpf_cliente": processo.get("cpf") or "",
        "empresa": processo.get("empresa") or "",
        "representante_nome": representante.get("nome") or "",
        "representante_cpf": representante.get("cpf") or "",
        "assinatura_cliente_url": assinatura,
        "termo_dados": termo,
    }
