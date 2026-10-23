"""Turn an incoming file into plain text, keeping line structure for anchors."""
import io
import re
import subprocess

from django.conf import settings

from . import ocr

PLAIN_TYPES = ("text/plain", "message/rfc822", "text/csv")


def extract_text(filename: str, content_type: str, data: bytes):
    """Returns (text, method, page_count)."""
    name = filename.lower()
    if name.endswith(".pdf") or content_type == "application/pdf" or data[:5] == b"%PDF-":
        return _pdf(data)
    if name.endswith((".txt", ".eml", ".csv", ".md")) or content_type in PLAIN_TYPES:
        return _clean(data.decode("utf-8", errors="replace")), "plain", 1
    if name.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff")):
        if settings.LEXFLOW_OCR_ENABLED:
            try:
                pages = ocr.image_text(data)
                text = _clean("\n\n".join(pages))
                if text.strip() and all(page.strip() for page in pages):
                    return text, "ocr", len(pages)
                return text, "ocr_partial", len(pages)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                pass
        return "", "needs_ocr", 1
    return "", "unsupported", 0


def _pdf(data: bytes):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages, used_ocr, incomplete = [], False, False
    for index, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except Exception:  # layout mode is not available for every PDF
            text = page.extract_text() or ""
        # A page with only a footer must not conceal an unread scan. Always mark
        # mixed/partial inputs for original-source review, even with OCR off.
        if len(text.strip()) < 40:
            if settings.LEXFLOW_OCR_ENABLED and len(reader.pages) <= settings.LEXFLOW_OCR_MAX_PAGES:
                try:
                    scanned = ocr.pdf_page_text(data, index)
                    text = scanned if scanned.strip() else text
                    used_ocr = True
                    incomplete |= not scanned.strip()
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    incomplete = True
            else:
                incomplete = True
        pages.append(text)
    text = "\n\n".join(pages)
    method = "ocr_partial" if incomplete and used_ocr else "needs_ocr" if incomplete else "pdf_ocr" if used_ocr else "pdf_text"
    return _clean(text), method, len(reader.pages)


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    lines = [re.sub(r"[ \t]{2,}", "  ", ln).rstrip() for ln in text.split("\n")]
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    # Layout extraction separates table rows with blank lines; rows start indented.
    out = re.sub(r"\n\n(?= \S)", "\n", out)
    return out.strip("\n")
