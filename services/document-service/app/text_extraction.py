"""Extract text from a stored document.

PDFs are parsed with pypdf first. If a PDF yields little/no embedded text it is
treated as a scanned document and routed to OCR (pytesseract via pillow).
Images go straight to OCR. All native-dependency failures (missing tesseract
binary, unreadable file) degrade gracefully: the function returns whatever text
it could obtain plus a clear note in ``extraction_method`` instead of raising.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from app.config import settings


@dataclass
class ExtractionResult:
    extracted_text: str
    doc_type: str
    page_count: int
    extraction_method: str


_PDF_MAGIC = b"%PDF-"
_IMAGE_CONTENT_PREFIXES = ("image/",)
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp")


def _looks_like_pdf(data: bytes, content_type: str, filename: str) -> bool:
    if data[:5] == _PDF_MAGIC:
        return True
    if content_type and "pdf" in content_type.lower():
        return True
    return filename.lower().endswith(".pdf")


def _looks_like_image(content_type: str, filename: str) -> bool:
    if content_type and content_type.lower().startswith(_IMAGE_CONTENT_PREFIXES):
        return True
    return filename.lower().endswith(_IMAGE_EXTENSIONS)


def _extract_pdf_text(data: bytes) -> tuple[str, int]:
    """Return (text, page_count) using pypdf. Never raises on per-page errors."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = reader.pages
    chunks: list[str] = []
    for page in pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            chunks.append("")
    return "\n".join(chunks).strip(), len(pages)


def _ocr_image_bytes(data: bytes) -> str:
    from PIL import Image
    import pytesseract

    image = Image.open(io.BytesIO(data))
    return (pytesseract.image_to_string(image, lang=settings.ocr_lang) or "").strip()


def _ocr_pdf(data: bytes) -> tuple[str, str]:
    """OCR a (scanned) PDF by rasterizing pages. Returns (text, method/note)."""
    # pypdf cannot rasterize; we rely on pdf2image if available, otherwise note
    # the limitation. pdf2image is optional and may be absent.
    try:
        from pdf2image import convert_from_bytes
    except Exception:
        return "", "ocr_unavailable: pdf rasterizer (pdf2image/poppler) not installed"

    try:
        images = convert_from_bytes(data)
    except Exception as exc:
        return "", f"ocr_unavailable: pdf rasterization failed ({exc})"

    import pytesseract

    chunks: list[str] = []
    for image in images:
        try:
            chunks.append((pytesseract.image_to_string(image, lang=settings.ocr_lang) or "").strip())
        except Exception as exc:
            return "\n".join(chunks).strip(), f"ocr_partial: {exc}"
    return "\n".join(chunks).strip(), "ocr_pytesseract"


def extract_text(*, data: bytes, content_type: str = "", filename: str = "") -> ExtractionResult:
    content_type = content_type or ""
    filename = filename or ""

    if _looks_like_pdf(data, content_type, filename):
        page_count = 0
        embedded_text = ""
        try:
            embedded_text, page_count = _extract_pdf_text(data)
        except Exception as exc:
            return ExtractionResult(
                extracted_text="",
                doc_type="pdf",
                page_count=0,
                extraction_method=f"failed: pdf parse error ({exc})",
            )

        if len(embedded_text) >= settings.ocr_min_text_chars:
            return ExtractionResult(
                extracted_text=embedded_text,
                doc_type="pdf",
                page_count=page_count,
                extraction_method="pypdf",
            )

        # Scanned / image-only PDF -> attempt OCR fallback.
        ocr_text, note = _ocr_pdf(data)
        text = ocr_text or embedded_text
        return ExtractionResult(
            extracted_text=text,
            doc_type="pdf",
            page_count=page_count,
            extraction_method=note if ocr_text else f"pypdf_low_text; {note}",
        )

    if _looks_like_image(content_type, filename):
        try:
            text = _ocr_image_bytes(data)
            return ExtractionResult(
                extracted_text=text,
                doc_type="image",
                page_count=1,
                extraction_method="ocr_pytesseract",
            )
        except Exception as exc:
            return ExtractionResult(
                extracted_text="",
                doc_type="image",
                page_count=1,
                extraction_method=f"ocr_unavailable: {exc}",
            )

    # Unknown type: try utf-8 text decode as a last resort.
    try:
        text = data.decode("utf-8", errors="replace").strip()
        return ExtractionResult(
            extracted_text=text,
            doc_type="text",
            page_count=1,
            extraction_method="utf8_decode",
        )
    except Exception as exc:
        return ExtractionResult(
            extracted_text="",
            doc_type="unknown",
            page_count=0,
            extraction_method=f"unsupported: {exc}",
        )
