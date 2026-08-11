"""PDF helpers: text-layer extraction and page rendering for the vision model."""

import base64
import io

import pypdfium2 as pdfium
from PIL import Image

RENDER_DPI = 150


def load_document(data: bytes, filename: str) -> tuple[str, list[bytes]]:
    """Return (text_layer, [png_page_bytes]) for a PDF or image upload."""
    name = filename.lower()
    if name.endswith((".jpg", ".jpeg", ".png")):
        img = Image.open(io.BytesIO(data)).convert("RGB")
        return "", [_to_png(img)]

    pdf = pdfium.PdfDocument(data)
    try:
        texts, pages = [], []
        for page in pdf:
            textpage = page.get_textpage()
            texts.append(textpage.get_text_range())
            textpage.close()
            bitmap = page.render(scale=RENDER_DPI / 72)
            pages.append(_to_png(bitmap.to_pil()))
            page.close()
        return "\n".join(texts), pages
    finally:
        pdf.close()


def _to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def to_data_url(png_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode()
