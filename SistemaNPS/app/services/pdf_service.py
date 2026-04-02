import base64
import httpx
from io import BytesIO
from typing import List, Optional, Any
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from app.services.pdf_layout import draw_header_footer, content_top, content_bottom, draw_wrapped_text
from app.routers.utils import normalize_base64

def _decode_to_image_reader(img_src: str) -> Optional[ImageReader]:
    """Converte uma string (Base64 ou URL) em um objeto ImageReader do ReportLab."""
    if not img_src:
        return None
    try:
        if img_src.startswith("data:"):
            _, raw = img_src.split(",", 1)
            return ImageReader(BytesIO(base64.b64decode(normalize_base64(raw))))
        else:
            resp = httpx.get(img_src, timeout=10)
            resp.raise_for_status()
            return ImageReader(BytesIO(resp.content))
    except Exception:
        return None

def gerar_pdf_termo_buffer(data: Any) -> BytesIO:
    """Gera o buffer do PDF para o Termo de Aceite."""
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margem_x = 40
    max_width = width - 80

    draw_header_footer(c, width, height)
    y = content_top(height)

    termo_dados = data.termo_dados or {}
    campos = dict(termo_dados.get("campos") or {})
    assinaturas = termo_dados.get("assinaturas") or {}
    aprovacao = termo_dados.get("aprovacao") or {}
    data_info = termo_dados.get("data") or {}

    # Cabecalho do Documento
    c.setFont("Helvetica-Bold", 16)
    c.drawString(margem_x, y, "TERMO DE ACEITE E ENTREGA DE SERVIÇOS")
    y -= 22
    c.setFont("Helvetica-Oblique", 12)
    c.drawString(margem_x, y, "UNIDADES MÓVEIS")
    y -= 30

    # 1. Data
    dia = data_info.get("dia")
    mes = data_info.get("mes")
    ano = data_info.get("ano")
    data_str = f"{dia}/{mes}/{ano}" if dia and mes and ano else ""
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margem_x, y, "Data")
    y -= 14
    c.setFont("Helvetica", 10)
    c.drawString(margem_x, y, data_str or "-")
    y -= 20

    # 2. Nome do Cliente
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margem_x, y, "Nome do Cliente")
    y -= 14
    c.setFont("Helvetica", 10)
    y = draw_wrapped_text(c, data.nome_cliente, margem_x, y, max_width, max_lines=2)
    y -= 10

    # 3. Empresa
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margem_x, y, "Empresa")
    y -= 14
    c.setFont("Helvetica", 10)
    y = draw_wrapped_text(c, data.empresa or "-", margem_x, y, max_width, max_lines=2)
    y -= 10

    # 4. Campos Dinamicos (Produto, Responsavel, Atendimento, Local)
    for key, value in campos.items():
        k_str = str(key).strip()
        # Evita duplicar campos que ja tratamos manualmente acima
        if k_str.upper() in ["NOME DO CLIENTE", "EMPRESA", "REGIÃO DA FOTO"]:
            continue

        if y < content_bottom() + 70:
            c.showPage()
            draw_header_footer(c, width, height)
            y = content_top(height)
        
        c.setFont("Helvetica-Bold", 11)
        c.drawString(margem_x, y, k_str.capitalize()[:80])
        y -= 14
        c.setFont("Helvetica", 10)
        y = draw_wrapped_text(c, str(value), margem_x, y, max_width, max_lines=3)
        y -= 10

    # 5. Status da Entrega
    status_map = {
        "concluido": "Concluído",
        "concluido_com_ressalva": "Concluído com Ressalva",
    }
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margem_x, y, "Status da Entrega")
    y -= 14
    c.setFont("Helvetica", 10)
    c.drawString(margem_x, y, status_map.get(data.status_entrega, data.status_entrega or "-"))
    y -= 25

    # Assinaturas
    if y < content_bottom() + 100:
        c.showPage()
        draw_header_footer(c, width, height)
        y = content_top(height)

    comprador = assinaturas.get("comprador") or {}
    rep_comercial = assinaturas.get("representante") or {}

    c.setFont("Helvetica-Bold", 12)
    c.drawString(margem_x, y, "Assinaturas")
    y -= 20
    
    c.setFont("Helvetica", 9)
    c.drawString(margem_x, y, f"Entregue a: {comprador.get('nome', '-')}")
    c.drawString(margem_x + 250, y, f"CPF: {comprador.get('cpf', '-')}")
    y -= 14
    c.drawString(margem_x, y, f"Representante Comercial: {rep_comercial.get('nome', '-')}")
    c.drawString(margem_x + 250, y, f"CPF: {rep_comercial.get('cpf', '-')}")
    y -= 25
    
    # Aprovação final
    if aprovacao.get("representante") or aprovacao.get("cpf"):
        c.setFont("Helvetica-Bold", 11)
        c.drawString(margem_x, y, "Aprovação final do termo")
        y -= 14
        c.setFont("Helvetica", 9)
        c.drawString(margem_x, y, f"Aprovado por: {aprovacao.get('representante', '-')}")
        c.drawString(margem_x + 250, y, f"CPF: {aprovacao.get('cpf', '-')}")
        y -= 20

    # Fotos
    imagens = termo_dados.get("itens") or []
    if imagens:
        chunk_size = 6
        for i in range(0, len(imagens), chunk_size):
            c.showPage()
            draw_header_footer(c, width, height)
            y = content_top(height)
            c.setFont("Helvetica-Bold", 15)
            c.drawString(margem_x, y, "FOTOS DO TERMO")
            
            chunk = imagens[i : i + chunk_size]
            # Layout de grid para fotos
            _draw_image_grid(c, chunk, margem_x, y - 20, max_width)

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

def gerar_pdf_ressalvas_buffer(responsavel: str, cpf: Optional[str], imagens: List[Any]) -> BytesIO:
    """Gera o buffer do PDF para as Ressalvas."""
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margem_x = 40
    max_width = width - 80

    cards_per_page = 3
    for i in range(0, len(imagens), cards_per_page):
        if i > 0:
            c.showPage()
        
        draw_header_footer(c, width, height)
        y = content_top(height)
        chunk = imagens[i : i + cards_per_page]
        
        c.setFont("Helvetica-Bold", 15)
        c.drawString(margem_x, y, f"Itens de Ressalva ({i+1} a {i+len(chunk)})")
        y -= 25

        card_h = (y - content_bottom() - 60) / cards_per_page
        for j, item in enumerate(chunk):
            card_top = y - j * (card_h + 10)
            c.rect(margem_x, card_top - card_h, max_width, card_h, stroke=1, fill=0)

            # Imagem do Card
            img_reader = _decode_to_image_reader(item.imagem_base64)
            if img_reader:
                c.drawImage(img_reader, margem_x + 5, card_top - card_h + 5, width=140, height=card_h - 10, preserveAspectRatio=True)

            # Texto do Card
            tx = margem_x + 155
            c.setFont("Helvetica-Bold", 9)
            c.drawString(tx, card_top - 15, f"Descrição: {item.descricao[:50]}")
            
            c.setFont("Helvetica", 8)
            # Campo Prazo
            prazo_str = item.prazo.strftime('%d/%m/%Y') if hasattr(item.prazo, 'strftime') else str(item.prazo or "-")
            c.drawString(tx, card_top - 27, f"Prazo: {prazo_str}")
            
            # Campo Responsável do Item
            c.drawString(tx, card_top - 38, f"Responsável: {item.responsavel or '-'}")
            
            # Observação
            draw_wrapped_text(c, f"Obs: {item.observacao or ''}", tx, card_top - 49, max_width - 160)

    # Assinatura final se for a ultima pagina
    apro_y = content_bottom() + 20
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margem_x, apro_y, f"Aprovação Final das Ressalvas: {responsavel} | CPF: {cpf or ''}")

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

def _draw_image_grid(c, images: List[Any], x: float, top_y: float, max_width: float):
    """Helper interno para desenhar o grid de fotos do termo."""
    label_map = {
        "frontal": "Frontal",
        "traseira": "Traseira",
        "lateral-esquerda": "Lateral Esquerda",
        "lateral-direita": "Lateral Direita",
        "superior": "Superior",
        "inferior": "Inferior",
    }

    cols, rows = 2, 3
    gap = 20
    cell_w = (max_width - gap) / cols
    cell_h = (top_y - content_bottom() - (gap * 3)) / rows

    for idx, img_data in enumerate(images):
        col = idx % cols
        row = idx // cols
        cx = x + (col * (cell_w + gap))
        cy = top_y - (row * (cell_h + gap))
        
        # Borda da imagem
        c.setStrokeColor(HexColor("#D1D1D1"))
        c.rect(cx, cy - cell_h, cell_w, cell_h, stroke=1, fill=0)
        
        img_reader = _decode_to_image_reader(img_data.get("imagem_base64"))
        if img_reader:
            c.drawImage(img_reader, cx + 2, cy - cell_h + 2, width=cell_w - 4, height=cell_h - 4, preserveAspectRatio=True, anchor="c")
        
        regiao = img_data.get("regiao_foto")
        label = label_map.get(regiao, regiao or f"Foto {idx + 1}")
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(HexColor("#000000"))
        c.drawString(cx, cy + 5, label.upper())
