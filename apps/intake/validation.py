from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError

ALLOWED_EXT = (".pdf", ".txt", ".eml", ".png", ".jpg", ".jpeg", ".tif", ".tiff")


def read_uploads(uploads):
    uploads = list(uploads)
    if len(uploads) > settings.LEXFLOW_MAX_FILES:
        raise ValidationError("Too many files in one intake.")
    files = []
    total = 0
    for upload in uploads:
        if upload.size > settings.LEXFLOW_MAX_UPLOAD_MB * 1024 * 1024:
            raise ValidationError("A file is larger than the configured upload limit.")
        total += upload.size
        if total > settings.LEXFLOW_MAX_TOTAL_MB * 1024 * 1024:
            raise ValidationError("The combined upload is too large.")
        files.append((upload.name, upload.content_type, upload.read()))
    return files


def validate_intake(subject, body, files):
    if not subject.strip():
        raise ValidationError("Add a subject so the office can recognise this intake.")
    if not files and not body.strip():
        raise ValidationError("Attach at least one document or paste the email text.")
    if len(files) > settings.LEXFLOW_MAX_FILES:
        raise ValidationError("Too many files in one intake.")
    total = len(body.encode())
    for name, content_type, data in files:
        if Path(name).name != name or any(c in name for c in ('\r', '\n', '"', '\\')):
            raise ValidationError("The document filename is invalid.")
        if not name.lower().endswith(ALLOWED_EXT):
            raise ValidationError("Only PDF, text and image files are accepted.")
        if not data:
            raise ValidationError("Empty documents cannot be processed.")
        if len(data) > settings.LEXFLOW_MAX_UPLOAD_MB * 1024 * 1024:
            raise ValidationError("A file is larger than the configured upload limit.")
        total += len(data)
    if total > settings.LEXFLOW_MAX_TOTAL_MB * 1024 * 1024:
        raise ValidationError("The combined intake is too large.")
