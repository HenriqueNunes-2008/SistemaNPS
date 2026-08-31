from __future__ import annotations

import base64
import json
from datetime import date, datetime, timezone
from io import BytesIO
from typing import Any

import httpx
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.services.pdf_layout import content_bottom, content_top, draw_header_footer
from app.services.processo_repository import ProcessoRepository
from app.services.supabase_client import supabase
from app.services.upload import upload_pdf
from app.services.shared_data import dados_compartilhados
from app.services.assinatura_service import gerar_url_assinatura


def _format_cpf_display(value: Any) -> str:
    raw = str(value or '').strip()
    if not raw or raw == 'Não informado':
        return 'Não informado' if raw == 'Não informado' else '-'
    digits = ''.join(ch for ch in raw if ch.isdigit())[:11]
    if len(digits) != 11:
        return raw
    return f'{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:11]}'


def _format_date_display(value: Any) -> str:
    if value is None or value == '':
        return '-'
    if isinstance(value, dict):
        dia = value.get('dia') or value.get('day') or ''
        mes = value.get('mes') or value.get('month') or ''
        ano = value.get('ano') or value.get('year') or ''
        if dia and mes and ano:
            return f'{int(dia):02d}/{int(mes):02d}/{ano}'
        return '-'
    if hasattr(value, 'strftime'):
        return value.strftime('%d/%m/%Y')
    texto = str(value).strip()
    if not texto or texto == 'Não informado':
        return '-' if not texto else 'Não informado'
    if len(texto) == 10 and '/' in texto:
        return texto
    if len(texto) == 8 and texto.isdigit():
        return f'{texto[:2]}/{texto[2:4]}/{texto[4:8]}'
    if len(texto) == 10 and '-' in texto:
        partes = texto.split('-')
        if len(partes) == 3:
            return f'{partes[2]}/{partes[1]}/{partes[0]}'
    return texto


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _decode_base64_image(image_base64: str) -> bytes | None:
    if not image_base64 or "," not in image_base64:
        return None
    try:
        _, raw = image_base64.split(",", 1)
        return base64.b64decode(raw)
    except Exception:
        return None


def _download_image(url: str) -> bytes | None:
    try:
        resp = httpx.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None


def _draw_wrapped(
    c,
    text: str,
    x: float,
    y: float,
    max_width: float,
    font_name: str = "Helvetica",
    font_size: int = 10,
    line_height: int = 13,
    max_lines: int | None = None,
) -> float:
    c.setFont(font_name, font_size)
    words = _safe_text(text).split()
    if not words:
        return y - line_height

    line = ""
    lines_drawn = 0
    for word in words:
        test_line = f"{line} {word}".strip()
        if c.stringWidth(test_line, font_name, font_size) <= max_width:
            line = test_line
            continue
        if line:
            c.drawString(x, y, line)
            y -= line_height
            lines_drawn += 1
            if max_lines and lines_drawn >= max_lines:
                return y
        line = word

    if line and (not max_lines or lines_drawn < max_lines):
        c.drawString(x, y, line)
        y -= line_height
    return y


def _collect_termo_images(proc_data: dict) -> list[dict]:
    ordered_regions = [
        "frontal",
        "traseira",
        "lateral-esquerda",
        "lateral-direita",
        "superior",
        "inferior",
    ]
    label_map = {
        "frontal": "Frontal",
        "traseira": "Traseira",
        "lateral-esquerda": "Lateral esquerda",
        "lateral-direita": "Lateral direita",
        "superior": "Superior",
        "inferior": "Inferior",
    }

    termo_dados = _as_dict(proc_data.get("termo_dados"))
    fontes: list[Any] = []
    fontes.extend(_as_list(termo_dados.get("itens")))
    fontes.extend(_as_list(termo_dados.get("imagens")))
    fontes.extend(_as_list(termo_dados.get("fotos")))
    fontes.extend(_as_list(proc_data.get("imagens_termo")))

    # Dedupe por regiao: a ultima imagem valida encontrada vence.
    por_regiao: dict[str, dict] = {}
    extras: list[dict] = []
    for idx, img in enumerate(fontes):
        if not isinstance(img, dict):
            continue

        regiao = _safe_text(img.get("regiao_foto")).strip().lower()
        raw_bytes = None
        
        # Tenta pegar a imagem do campo imagem_base64 (que agora pode ser URL ou Base64)
        img_src = img.get("imagem_base64") or img.get("imagem")
        if img_src:
            if str(img_src).startswith("data:"):
                raw_bytes = _decode_base64_image(img_src)
            elif str(img_src).startswith("http"):
                raw_bytes = _download_image(img_src)
        elif img.get("url"):
            raw_bytes = _download_image(img.get("url"))

        if not raw_bytes:
            continue

        item_val = img.get("item")
        try:
            item_order = int(item_val)
        except Exception:
            item_order = idx + 1

        payload = {
            "label": label_map.get(regiao, regiao or f"Foto {idx + 1}"),
            "bytes": raw_bytes,
            "order": item_order,
        }

        if regiao in ordered_regions:
            por_regiao[regiao] = payload
        else:
            extras.append(payload)

    resultado: list[dict] = []
    for regiao in ordered_regions:
        if regiao in por_regiao:
            resultado.append(por_regiao[regiao])

    # Completa eventuais faltas com imagens sem regiao conhecida.
    if len(resultado) < 6 and extras:
        extras.sort(key=lambda x: x.get("order", 999))
        for extra in extras:
            if len(resultado) >= 6:
                break
            resultado.append(extra)

    return resultado[:6]


def _collect_ressalvas_items(proc_data: dict) -> list[dict]:
    ressalvas_dados = _as_dict(proc_data.get("ressalvas_dados"))
    itens = _as_list(ressalvas_dados.get("itens"))

    resultado = []
    for idx, item in enumerate(itens):
        if not isinstance(item, dict):
            continue
        
        img_src = item.get("imagem_base64")
        raw_bytes = None
        if img_src:
            if str(img_src).startswith("data:"): raw_bytes = _decode_base64_image(img_src)
            elif str(img_src).startswith("http"): raw_bytes = _download_image(img_src)

        resultado.append(
            {
                "idx": idx + 1,
                "item": _safe_text(item.get("item", "")),
                "descricao": _safe_text(item.get("descricao", "")),
                "prazo": _safe_text(item.get("prazo", "")),
                "responsavel": _safe_text(item.get("responsavel", "")),
                "regiao_foto": _safe_text(item.get("regiao_foto", "")),
                "bytes": raw_bytes,
            }
        )
    return resultado[:6]


def _draw_termo_info_page(c, width: float, height: float, proc_data: dict) -> None:
    draw_header_footer(c, width, height)
    y = content_top(height)
    x = 40
    max_width = width - 80

    termo_dados = _as_dict(proc_data.get("termo_dados"))
    campos = _as_dict(termo_dados.get("campos"))
    assinaturas = _as_dict(termo_dados.get("assinaturas"))
    data_info = _as_dict(termo_dados.get("data"))

    produto_codigo = (
        campos.get("PRODUTO E CÓDIGO DA ENTREGA")
        or campos.get("Produto e Código da Entrega")
        or campos.get("produto_codigo_entrega")
        or campos.get("Produto")
        or campos.get("produto")
        or "-"
    )
    c.setFont("Helvetica-Bold", 15)
    c.drawString(x, y, "1/5 - Informacoes do termo de aceite")
    y -= 22
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x, y, "Unidade móvel em questão")
    y -= 14
    c.setFont("Helvetica", 10)
    c.drawString(x, y, f"- {produto_codigo}")
    y -= 18

    c.setFont("Helvetica-Bold", 11)
    c.drawString(x, y, "Nome do cliente")
    y -= 14
    c.setFont("Helvetica", 10)
    y = _draw_wrapped(c, _safe_text(proc_data.get("nome_cliente")), x, y, max_width, max_lines=2)
    y -= 4

    c.setFont("Helvetica-Bold", 11)
    c.drawString(x, y, "Empresa")
    y -= 14
    c.setFont("Helvetica", 10)
    y = _draw_wrapped(c, _safe_text(proc_data.get("empresa")), x, y, max_width, max_lines=2)
    y -= 4

    if isinstance(data_info, dict):
        data_str = _format_date_display(data_info)
        if data_str and data_str != '-':
            c.setFont("Helvetica-Bold", 11)
            c.drawString(x, y, "Data")
            y -= 14
            c.setFont("Helvetica", 10)
            c.drawString(x, y, data_str)
            y -= 18

    c.setFont("Helvetica", 10)
    for key, value in campos.items():
        if y < content_bottom() + 70:
            break
        
        k_str = _safe_text(key).strip()
        if k_str.upper() in ["NOME DO CLIENTE", "EMPRESA", "REGIÃO DA FOTO"]:
            continue

        c.setFont("Helvetica-Bold", 10)
        c.drawString(x, y, k_str.capitalize()[:80])
        y -= 13
        c.setFont("Helvetica", 10)
        y = _draw_wrapped(c, _safe_text(value), x, y, max_width, max_lines=3)
        y -= 6

    if y > content_bottom() + 75:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x, y, "Status da entrega")
        y -= 13
        c.setFont("Helvetica", 10)
        c.drawString(x, y, _safe_text(proc_data.get("status_entrega")))
        y -= 18

    c.setFont("Helvetica", 9)
    _draw_wrapped(
        c,
        "Por estarem assim ajustadas, as partes assinam o presente termo dando por encerradas todas as responsabilidades e atividades referentes aos serviços de customização.",
        x,
        content_bottom() + 125,
        max_width,
        max_lines=3,
    )
    _draw_client_signature(c, x, content_bottom() + 20, proc_data)


def _draw_client_signature(c, x: float, y: float, proc_data: dict) -> None:
    src = proc_data.get("assinatura_cliente_url") or _as_dict(proc_data.get("termo_dados")).get("assinatura_cliente_path")
    if src:
        try:
            url = src if str(src).startswith("http") else gerar_url_assinatura(src)
            raw = _download_image(url) if url else None
            if raw:
                c.drawImage(ImageReader(BytesIO(raw)), x, y, width=160, height=45, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass
    c.setFont("Helvetica", 8); c.drawString(x, y - 8, "Assinatura digital do cliente")



def _draw_termo_images_page(c, width: float, height: float, images: list[dict]) -> None:
    draw_header_footer(c, width, height)
    x = 40
    y = content_top(height)
    max_width = width - 80

    c.setFont("Helvetica-Bold", 15)
    c.drawString(x, y, "2/5 - Fotos do termo (6 imagens)")

    cols = 2
    rows = 3
    gap_x = 12
    gap_y = 14
    top_y = y - 20
    cell_w = (max_width - gap_x) / cols
    cell_h = ((top_y - (content_bottom() + 20)) - (gap_y * (rows - 1))) / rows

    for idx in range(6):
        col = idx % cols
        row = idx // cols
        cx = x + (col * (cell_w + gap_x))
        cy_top = top_y - (row * (cell_h + gap_y))
        img_y = cy_top - cell_h + 2

        label = f"Foto {idx + 1}"
        raw_bytes = None
        if idx < len(images):
            label = images[idx].get("label", label)
            raw_bytes = images[idx].get("bytes")

        c.setFont("Helvetica-Bold", 9)
        c.drawString(cx, cy_top, label)
        c.rect(cx, img_y, cell_w, cell_h - 12, stroke=1, fill=0)

        if raw_bytes:
            try:
                c.drawImage(
                    ImageReader(BytesIO(raw_bytes)),
                    cx + 3,
                    img_y + 3,
                    width=cell_w - 6,
                    height=cell_h - 18,
                    preserveAspectRatio=True,
                    anchor="c",
                    mask="auto",
                )
            except Exception:
                pass
        else:
            c.setFont("Helvetica", 8)
            c.drawString(cx + 6, img_y + (cell_h / 2) - 6, "Imagem nao informada")


def _draw_ressalvas_page(
    c,
    width: float,
    height: float,
    items: list[dict],
    title: str,
    start_index: int,
    proc_data: dict | None = None,
) -> None:
    draw_header_footer(c, width, height)
    x = 40
    y = content_top(height)
    max_width = width - 80

    c.setFont("Helvetica-Bold", 15)
    c.drawString(x, y, title)
    y -= 20

    card_gap = 10
    cards = 3
    card_h = ((y - (content_bottom() + 20)) - (card_gap * (cards - 1))) / cards

    for i in range(cards):
        idx = start_index + i
        card_top = y - i * (card_h + card_gap)
        c.rect(x, card_top - card_h, max_width, card_h, stroke=1, fill=0)

        if idx >= len(items):
            c.setFont("Helvetica", 9)
            c.drawString(x + 8, card_top - 18, f"Item {idx + 1} - nao informado")
            continue

        item = items[idx]
        img_w = 150
        img_h = card_h - 20
        img_x = x + 8
        img_y = card_top - card_h + 10

        c.rect(img_x, img_y, img_w, img_h, stroke=1, fill=0)
        if item.get("bytes"):
            try:
                c.drawImage(
                    ImageReader(BytesIO(item["bytes"])),
                    img_x + 2,
                    img_y + 2,
                    width=img_w - 4,
                    height=img_h - 4,
                    preserveAspectRatio=True,
                    anchor="c",
                    mask="auto",
                )
            except Exception:
                pass

        tx = img_x + img_w + 8
        ty = card_top - 14
        tw = max_width - (img_w + 24)

        c.setFont("Helvetica-Bold", 9)
        c.drawString(tx, ty, f"Item {item.get('idx')}: {_safe_text(item.get('item'))[:55]}")
        ty -= 12
        c.setFont("Helvetica", 8)
        c.drawString(tx, ty, f"Regiao: {_safe_text(item.get('regiao_foto'))[:40]}")
        ty -= 11
        c.drawString(tx, ty, f"Prazo: {_safe_text(item.get('prazo'))[:30]}")
        ty -= 11
        c.drawString(tx, ty, f"Responsavel: {_safe_text(item.get('responsavel'))[:35]}")
        ty -= 11
        _draw_wrapped(
            c,
            f"Descricao: {_safe_text(item.get('descricao'))}",
            tx,
            ty,
            tw,
            font_size=8,
            line_height=10,
            max_lines=5,
        )

    _draw_client_signature(c, x, content_bottom() + 18, proc_data or {})


def _draw_extra_term_page(c, width: float, height: float, proc_data: dict, tipo: str, title: str) -> None:
    draw_header_footer(c, width, height)
    x, y, max_width = 40, content_top(height), width - 80
    d = dados_compartilhados(proc_data)
    termo_extra = _as_dict(proc_data.get(f"{tipo}_dados"))
    d.update({key: value for key, value in termo_extra.items() if key not in d})
    c.setFont("Helvetica-Bold", 15); c.drawString(x, y, title); y -= 25
    c.setFont("Helvetica", 10)
    codigo = " - ".join(str(v) for v in (d.get("produto"), d.get("codigo_entrega")) if v) or "-"
    texto = (
        "Recebi da FLEXIMEDICAL SLOUÇÕES EM SAÚDE LTDA - CNPJ: 07.384.026/0001-20, "
        f"fabricante da UNIDADE MÓVEL EM QUESTÃO, {codigo}, o \"MANUAL DE INSTRUÇÕES DE USO\", "
        "que deverá ser consultado antes de qualquer intervenção de limpeza ou manuseio, sob o risco de perda da garantia."
    )
    if tipo == "treinamento":
        texto += " Afirmo também ter recebido treinamento adequado para manuseio do equipamento. Tornando-me responsável pelo treinamento das pessoas envolvidas no manuseio."
    y = _draw_wrapped(c, texto, x, y, max_width, max_lines=8); y -= 15
    for label, key in (("Cliente", "nome_cliente"), ("CPF", "cpf_cliente"), ("Produto e Código da Entrega", None), ("Representante", "representante_nome"), ("CPF do representante", "representante_cpf")):
        value = codigo if key is None else d.get(key)
        if label.upper().startswith("CPF"):
            value = _format_cpf_display(value)
        c.setFont("Helvetica-Bold", 10); c.drawString(x, y, f"{label}: {_safe_text(value)}"); y -= 14
    _draw_client_signature(c, x, y - 5, proc_data)


def _draw_nps_page(c, width: float, height: float, nps_dados: dict) -> None:
    draw_header_footer(c, width, height)
    x = 40
    y = content_top(height)
    max_width = width - 80

    nps_dados = _as_dict(nps_dados)
    nps_val = nps_dados.get("nps")
    avaliacoes = _as_dict(nps_dados.get("avaliacoes"))
    feedback = _as_dict(nps_dados.get("feedback"))

    c.setFont("Helvetica-Bold", 15)
    c.drawString(x, y, "5/5 - NPS com notas, feedback e avaliacoes")
    y -= 24

    c.setFont("Helvetica-Bold", 12)
    c.drawString(x, y, f"Nota NPS: {_safe_text(nps_val)}")
    y -= 22

    c.setFont("Helvetica-Bold", 11)
    c.drawString(x, y, "Avaliacoes")
    y -= 15
    c.setFont("Helvetica", 10)
    for chave, valor in avaliacoes.items():
        if y < content_bottom() + 90:
            break
        y = _draw_wrapped(c, f"- {chave}: {valor}", x, y, max_width, max_lines=2)

    y -= 8
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x, y, "Feedback")
    y -= 15
    for titulo, texto in feedback.items():
        if y < content_bottom() + 70:
            break
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x, y, _safe_text(titulo)[:80])
        y -= 13
        c.setFont("Helvetica", 10)
        y = _draw_wrapped(c, _safe_text(texto), x + 8, y, max_width - 8, max_lines=6)
        y -= 5


def _build_final_pdf_bytes(proc_data: dict) -> bytes:
    termo_images = _collect_termo_images(proc_data)
    ressalvas_items = _collect_ressalvas_items(proc_data)
    nps_dados = proc_data.get("nps_dados") or {}

    final_buffer = BytesIO()
    c = canvas.Canvas(final_buffer, pagesize=A4)
    width, height = A4

    _draw_termo_info_page(c, width, height, proc_data)
    c.showPage()
    _draw_termo_images_page(c, width, height, termo_images)
    c.showPage()
    _draw_ressalvas_page(c, width, height, ressalvas_items, "2/5 - Ressalvas (itens 1 a 3)", 0, proc_data=proc_data)
    c.showPage()
    _draw_ressalvas_page(
        c,
        width,
        height,
        ressalvas_items,
        "2/5 - Ressalvas (itens 4 a 6)",
        3,
        proc_data=proc_data,
    )
    c.showPage()
    _draw_extra_term_page(c, width, height, proc_data, "recebimento", "3/5 - Termo de Recebimento")
    c.showPage()
    _draw_extra_term_page(c, width, height, proc_data, "treinamento", "4/5 - Termo de Treinamento")
    c.showPage()
    _draw_nps_page(c, width, height, nps_dados)
    c.showPage()
    c.save()
    final_buffer.seek(0)
    return final_buffer.read()


def regenerate_final_pdf_by_codigo(codigo: str, set_status_finalizado: bool = False) -> str | None:
    proc = ProcessoRepository.get_by_identifier(codigo, "id,codigo,nome_cliente,empresa,cpf,status_entrega,termo_dados,ressalvas_dados,imagens_termo,nps_dados,recebimento_dados,treinamento_dados,assinatura_cliente_url")
    if not proc:
        return None

    nps_dados = _as_dict(proc.get("nps_dados"))
    if not isinstance(nps_dados, dict) or "nps" not in nps_dados:
        return None

    pdf_bytes = _build_final_pdf_bytes(proc)
    final_base64 = "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode()
    final_url = upload_pdf(final_base64, f"{proc['id']}/final")
    if not final_url:
        raise RuntimeError("Falha no upload do PDF final")

    update_data = {
        "pdf_final": final_url,
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
    }
    if set_status_finalizado:
        update_data["status"] = "finalizado"
        update_data["finalizado_em"] = date.today().isoformat()

    supabase.table("processos").update(update_data).eq("id", proc["id"]).execute()
    return final_url
