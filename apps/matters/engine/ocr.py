"""Bounded, local OCR. No document bytes leave the office deployment."""
import io
import os
from pathlib import Path
import subprocess
import tempfile

from django.conf import settings


def _run(args, timeout):
    # stderr can contain filenames/document fragments; never expose it to users.
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          timeout=timeout, env={**os.environ, "OMP_THREAD_LIMIT": "1"}).stdout


def image_text(data):
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > settings.LEXFLOW_OCR_MAX_PIXELS:
            raise ValueError("Image exceeds the OCR pixel limit")
        if getattr(image, "n_frames", 1) > settings.LEXFLOW_OCR_MAX_PAGES:
            raise ValueError("Image exceeds the OCR page limit")
        texts = []
        with tempfile.TemporaryDirectory(prefix="lexflow-ocr-", dir=settings.LEXFLOW_OCR_TEMP_ROOT) as temp:
            for frame in range(getattr(image, "n_frames", 1)):
                image.seek(frame)
                if image.width * image.height > settings.LEXFLOW_OCR_MAX_PIXELS:
                    raise ValueError("Image exceeds the OCR pixel limit")
                path = Path(temp) / "page.png"
                image.convert("RGB").save(path)
                path.chmod(0o600)
                texts.append(_run([settings.LEXFLOW_TESSERACT, str(path), "stdout", "-l",
                                   settings.LEXFLOW_OCR_LANGUAGES, "--psm", "6"],
                                  settings.LEXFLOW_OCR_TIMEOUT).decode("utf-8"))
        return texts


def pdf_page_text(data, page):
    with tempfile.TemporaryDirectory(prefix="lexflow-ocr-", dir=settings.LEXFLOW_OCR_TEMP_ROOT) as temp:
        source, output = Path(temp) / "source.pdf", Path(temp) / "page"
        source.write_bytes(data)
        source.chmod(0o600)
        _run([settings.LEXFLOW_PDFTOPPM, "-f", str(page), "-l", str(page), "-singlefile",
              "-r", "160", "-scale-to", "2200", "-png", str(source), str(output)],
             settings.LEXFLOW_OCR_TIMEOUT)
        return image_text(output.with_suffix(".png").read_bytes())[0]


def available():
    """Probe both binaries and the actual configured language packs."""
    try:
        langs = _run([settings.LEXFLOW_TESSERACT, "--list-langs"], 5).decode().splitlines()
        _run([settings.LEXFLOW_PDFTOPPM, "-v"], 5)
        return all(lang in langs for lang in settings.LEXFLOW_OCR_LANGUAGES.split("+"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
