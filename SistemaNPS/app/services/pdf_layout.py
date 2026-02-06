import os
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader

# Layout constants
HEADER_MARGIN_X = 30
HEADER_MARGIN_TOP = 20
HEADER_IMAGE_HEIGHT = 50
HEADER_BOTTOM_GAP = 20

FOOTER_Y = 20
FOOTER_FONT = "Helvetica"
FOOTER_FONT_SIZE = 10
FOOTER_COLOR = HexColor("#8a8a8a")

DEFAULT_FOOTER_TEXT = (
    "R. José Antônio Valadares, 285 - Vila Liviero, São Paulo - SP, 04185-020"
)


def _asset_path(filename: str) -> str:
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static"))
    return os.path.join(base, filename)


def _load_image(filename: str) -> ImageReader | None:
    path = _asset_path(filename)
    if not os.path.exists(path):
        return None
    try:
        return ImageReader(path)
    except Exception:
        return None


def draw_header_footer(
    c,
    width: float,
    height: float,
    footer_text: str = DEFAULT_FOOTER_TEXT
) -> None:
    # Header images
    left_img = _load_image("LogoFlexcolor.png")
    right_img = _load_image("LogoFlex2.png")

    if left_img:
        iw, ih = left_img.getSize()
        scale = HEADER_IMAGE_HEIGHT / float(ih)
        w = float(iw) * scale
        x = HEADER_MARGIN_X
        y = height - HEADER_MARGIN_TOP - HEADER_IMAGE_HEIGHT
        c.saveState()
        if hasattr(c, "setFillAlpha"):
            c.setFillAlpha(0.6)
        c.drawImage(left_img, x, y, width=w, height=HEADER_IMAGE_HEIGHT, mask="auto")
        c.restoreState()

    if right_img:
        iw, ih = right_img.getSize()
        scale = HEADER_IMAGE_HEIGHT / float(ih)
        w = float(iw) * scale
        x = width - HEADER_MARGIN_X - w
        y = height - HEADER_MARGIN_TOP - HEADER_IMAGE_HEIGHT
        c.drawImage(right_img, x, y, width=w, height=HEADER_IMAGE_HEIGHT, mask="auto")

    # Footer text
    c.setFont(FOOTER_FONT, FOOTER_FONT_SIZE)
    c.setFillColor(FOOTER_COLOR)
    text_w = c.stringWidth(footer_text, FOOTER_FONT, FOOTER_FONT_SIZE)
    c.drawString((width - text_w) / 2, FOOTER_Y, footer_text)
    c.setFillColor(HexColor("#000000"))


def content_top(height: float) -> float:
    return height - HEADER_MARGIN_TOP - HEADER_IMAGE_HEIGHT - HEADER_BOTTOM_GAP


def content_bottom() -> float:
    return FOOTER_Y + 30
