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
from app.services.assinatura_service import gerar_url_assinatura


def formatar_cpf_apresentacao(valor: Any) -> str:
    """Apresenta CPF em formato visual sem alterar o valor persistido."""
    raw = str(valor or '').strip()
    if not raw or raw == 'Não informado':
        return 'Não informado' if raw == 'Não informado' else '-'
    digits = ''.join(ch for ch in raw if ch.isdigit())[:11]
    if len(digits) != 11:
        return raw
    return f'{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:11]}'


def formatar_data_apresentacao(valor: Any) -> str:
    """Apresenta data em formato visual sem alterar o valor persistido."""
    if valor is None or valor == '':
        return '-'
    if isinstance(valor, dict):
        dia = valor.get('dia') or valor.get('day') or ''
        mes = valor.get('mes') or valor.get('month') or ''
        ano = valor.get('ano') or valor.get('year') or ''
        if dia and mes and ano:
            return f'{int(dia):02d}/{int(mes):02d}/{ano}'
        return '-'
    if hasattr(valor, 'strftime'):
        return valor.strftime('%d/%m/%Y')
    texto = str(valor).strip()
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
    if len(texto) == 10 and texto[4] == '-':
        return f'{texto[8:10]}/{texto[5:7]}/{texto[0:4]}'
    return texto


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
    data_info = termo_dados.get("data") or {}

    def campo(*nomes):
        for nome in nomes:
            for chave, valor in campos.items():
                if str(chave).strip().casefold() == nome.casefold():
                    return valor
        return ""

    produto_codigo = (
        campo("PRODUTO E CÓDIGO DA ENTREGA", "Produto e Código da Entrega", "produto_codigo_entrega")
        or termo_dados.get("produto_codigo_entrega")
        or campo("Produto", "produto")
        or "-"
    )
    data_str = formatar_data_apresentacao(data_info)
    comprador = assinaturas.get("comprador") or {}
    representante = assinaturas.get("representante") or {}
    campos_ordem = (
        ("Data", data_str),
        ("Nome do Cliente", data.nome_cliente),
        ("Empresa", data.empresa),
        ("Produto e Código da entrega", produto_codigo),
        ("Responsável pela entrega", campo("Responsável pela entrega")),
        ("Quem realizou o atendimento", campo("Quem realizou o atendimento")),
        ("Local da entrega", campo("Local da entrega")),
        ("Status da Entrega", {
            "concluido": "Concluído",
            "concluido_com_ressalva": "Concluído com Ressalva",
        }.get(data.status_entrega, data.status_entrega)),
    )

    c.setFont("Helvetica-Bold", 16)
    c.drawString(margem_x, y, "TERMO DE ACEITE E ENTREGA DE SERVIÇOS")
    y -= 22
    c.setFont("Helvetica-Oblique", 12)
    c.drawString(margem_x, y, f"UNIDADE MÓVEL EM QUESTÃO - {produto_codigo}")
    y -= 30
    c.setFont("Helvetica", 10)
    y = draw_wrapped_text(
        c,
        "Recebi da FLEXIMEDICAL SLOUÇÕES EM SAÚDE LTDA - CNPJ: 07.384.026/0001-20, os serviços de reforma da Unidade Móvel de Saúde em Questão.",
        margem_x,
        y,
        max_width,
        max_lines=3,
    ) - 10

    for label, value in campos_ordem:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(margem_x, y, label)
        y -= 14
        c.setFont("Helvetica", 10)
        y = draw_wrapped_text(c, str(value or "-"), margem_x, y, max_width, max_lines=2) - 8

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margem_x, y, f"Entregue a: {comprador.get('nome') or '-'}")
    c.drawString(margem_x + 280, y, f"CPF: {formatar_cpf_apresentacao(comprador.get('cpf'))}")
    y -= 16
    c.drawString(margem_x, y, f"Representante Comercial: {representante.get('nome') or '-'}")
    c.drawString(margem_x + 280, y, f"CPF: {formatar_cpf_apresentacao(representante.get('cpf'))}")
    y -= 28
    y = draw_wrapped_text(
        c,
        "Por estarem assim ajustadas, as partes assinam o presente termo dando por encerradas todas as responsabilidades e atividades referentes aos serviços de customização.",
        margem_x,
        y,
        max_width,
        max_lines=3,
    )
    _draw_signature(
        c,
        margem_x,
        y - 18,
        termo_dados.get("assinatura_cliente_url") or termo_dados.get("assinatura_cliente_path"),
    )

    imagens = termo_dados.get("itens") or []
    for offset in range(0, len(imagens), 6):
        c.showPage()
        draw_header_footer(c, width, height)
        fotos_y = content_top(height)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(margem_x, fotos_y, "FOTOS DO TERMO")
        _draw_image_grid(c, imagens[offset:offset + 6], margem_x, fotos_y - 20, max_width)

    c.save()
    buffer.seek(0)
    return buffer


def _draw_termo_final(c, x: float, max_width: float, termo_dados: dict, y: float) -> None:
    c.setFont("Helvetica", 10)
    y = draw_wrapped_text(
        c,
        "Por estarem assim ajustadas, as partes assinam o presente termo dando por encerradas todas as responsabilidades e atividades referentes aos serviços de customização.",
        x,
        y,
        max_width,
        max_lines=4,
    )
    _draw_signature(c, x, y - 20, termo_dados.get("assinatura_cliente_url") or termo_dados.get("assinatura_cliente_path"))


def _draw_signature(c, x: float, y: float, source: str | None) -> None:
    if source:
        try:
            url = source if str(source).startswith(("http", "data:")) else gerar_url_assinatura(source)
            reader = _decode_to_image_reader(url)
            if reader:
                c.drawImage(reader, x, y - 55, width=180, height=55, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass
    c.setFont("Helvetica", 8)
    c.line(x, y - 58, x + 180, y - 58)
    c.drawString(x, y - 70, "Assinatura digital do cliente")


def _gerar_pdf_termo_extra(data: dict, treinamento: bool) -> BytesIO:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    draw_header_footer(c, width, height)
    x, y, max_width = 40, content_top(height), width - 80
    titulo = "TERMO DE TREINAMENTO" if treinamento else "TERMO DE RECEBIMENTO"
    c.setFont("Helvetica-Bold", 16); c.drawString(x, y, titulo); y -= 20
    c.setFont("Helvetica-Oblique", 11); c.drawString(x, y, "Via da Kure / Fleximedical"); y -= 28
    c.setFont("Helvetica", 10)
    produto_codigo = " — ".join(str(v) for v in (data.get("produto"), data.get("codigo_entrega")) if v)
    texto_recebimento = (
        "Recebi da FLEXIMEDICAL SLOUÇÕES EM SAÚDE LTDA - CNPJ: 07.384.026/0001-20, "
        f"fabricante da UNIDADE MÓVEL EM QUESTÃO, {produto_codigo or '-'}, o \"MANUAL DE INSTRUÇÕES DE USO\", "
        "que deverá ser consultado antes de qualquer intervenção de limpeza ou manuseio, sob o risco de perda da garantia."
    )
    data_info = data.get("data")
    if isinstance(data_info, dict):
        data_info = formatar_data_apresentacao(data_info)
    elif data_info is None:
        data_info = '-'
    for label, value in (("Data", formatar_data_apresentacao(data_info)), ("Nome do Cliente", data.get("nome_cliente")),
                         ("CPF", formatar_cpf_apresentacao(data.get("cpf_cliente"))), ("Produto e Código da Entrega", produto_codigo),
                         ("Representante Fleximedical", data.get("representante_nome")),
                         ("CPF do representante", formatar_cpf_apresentacao(data.get("representante_cpf")))):
        c.setFont("Helvetica-Bold", 10); c.drawString(x, y, label); y -= 13
        c.setFont("Helvetica", 10); y = draw_wrapped_text(c, str(value or "-"), x, y, max_width, max_lines=2); y -= 8
    texto = texto_recebimento
    if treinamento:
        texto += " Afirmo também ter recebido treinamento adequado para manuseio do equipamento. Tornando-me responsável pelo treinamento das pessoas envolvidas no manuseio."
    y -= 8; y = draw_wrapped_text(c, texto, x, y, max_width, max_lines=10)
    _draw_signature(c, x, y - 15, data.get("assinatura_cliente_url"))
    c.showPage(); c.save(); buffer.seek(0); return buffer


def gerar_pdf_recebimento_buffer(data: dict) -> BytesIO:
    return _gerar_pdf_termo_extra(data, False)


def gerar_pdf_treinamento_buffer(data: dict) -> BytesIO:
    return _gerar_pdf_termo_extra(data, True)

def gerar_pdf_ressalvas_buffer(
    responsavel: str, cpf: Optional[str], imagens: List[Any], assinatura_cliente_url: str | None = None
) -> BytesIO:
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

    c.showPage()
    draw_header_footer(c, width, height)
    _draw_signature(c, margem_x, content_top(height) - 25, assinatura_cliente_url)
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
