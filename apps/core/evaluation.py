"""Read-only extraction evaluation. Reports contain field keys, never source values."""
import hashlib
import io
import json
from pathlib import Path
import time
from types import SimpleNamespace

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.matters.engine.aggregate import decide
from apps.matters.engine.classify import classify_document, classify_matter
from apps.matters.engine.providers import RuleProvider
from apps.matters.engine.text import extract_text
from apps.studio.models import DocumentType, MatterType


def document_bytes(document, base):
    """Accept real sanitized files or reproducible synthetic rendering recipes."""
    if "file" in document:
        path = (base / document["file"]).resolve()
        if not path.is_relative_to(base.resolve()) or not path.is_file():
            raise ValidationError("Evaluation files must stay inside the dataset directory.")
        if path.stat().st_size > settings.LEXFLOW_MAX_UPLOAD_MB * 1024 * 1024:
            raise ValidationError("Evaluation file exceeds the upload limit.")
        return path.name, "", path.read_bytes()
    text, fmt = document["text"], document.get("format", "text")
    if fmt == "text":
        return "sample.txt", "text/plain", text.encode()
    if fmt == "pdf":
        from reportlab.pdfgen import canvas
        output = io.BytesIO()
        drawing = canvas.Canvas(output)
        drawing.setFont("Helvetica", 12)
        for index, line in enumerate(text.splitlines()):
            drawing.drawString(45, 795 - index * 22, line)
        drawing.save()
        return "sample.pdf", "application/pdf", output.getvalue()
    if fmt in ("scan", "scanned_pdf"):
        from PIL import Image, ImageDraw, ImageFont
        font = ImageFont.truetype(settings.LEXFLOW_EVALUATION_FONT, 34)
        image = Image.new("RGB", (1800, max(800, (len(text.splitlines()) + 2) * 60)), "white")
        drawing = ImageDraw.Draw(image)
        for index, line in enumerate(text.splitlines()):
            drawing.text((55, 50 + index * 60), line, fill="black", font=font)
        output = io.BytesIO()
        image.save(output, format="PDF" if fmt == "scanned_pdf" else "PNG", resolution=160)
        return ("scan.pdf", "application/pdf", output.getvalue()) if fmt == "scanned_pdf" else ("scan.png", "image/png", output.getvalue())
    raise ValidationError("Unknown synthetic evaluation document format.")


def evaluate(office, dataset_path):
    dataset_path = Path(dataset_path)
    encoded = dataset_path.read_bytes()
    dataset = json.loads(encoded)
    if dataset.get("version") != 1 or not dataset.get("cases"):
        raise ValidationError("Supply a nonempty version-1 evaluation dataset.")
    matter_types = list(MatterType.objects.filter(tenant=office, is_active=True))
    doc_types = list(DocumentType.objects.filter(tenant=office))
    by_type = {mt.key: mt for mt in matter_types}
    provider = RuleProvider()  # Evaluation never enables an external provider.
    results, assertions, passed, conflicts, conflict_matches, empty, empty_matches = [], 0, 0, 0, 0, 0, 0
    ids = set()
    for case in dataset["cases"]:
        if not case.get("id") or case["id"] in ids:
            raise ValidationError("Evaluation case IDs must be present and unique.")
        ids.add(case["id"])
        mt = by_type.get(case["matter_type"])
        if not mt:
            raise ValidationError("An evaluation case references an unavailable office workflow.")
        definitions = list(mt.fields.all())
        known = {definition.key for definition in definitions}
        if not (set(case.get("expected", {})) | set(case.get("conflicts", []))) <= known:
            raise ValidationError("Evaluation expectations reference unknown field keys.")
        candidates, decisions, methods, checksums = [], [], [], []
        started = time.monotonic()
        for document in case["documents"]:
            filename, content_type, data = document_bytes(document, dataset_path.parent)
            text, method, _ = extract_text(filename, content_type, data)
            checksum = hashlib.sha256(data).hexdigest()
            attachment = SimpleNamespace(display_name=filename, sha256=checksum)
            candidates.extend(provider.extract(attachment, text, definitions))
            decisions.append(classify_document(text, doc_types))
            methods.append(method)
            checksums.append(checksum)
        values = {definition.key: decide(definition, [c for c in candidates if c.field_key == definition.key]) for definition in definitions}
        classification = classify_matter(case.get("subject", ""), decisions, matter_types)
        failures = []
        case_checks = 1
        if classification.matter_type is None or classification.matter_type.key != mt.key:
            failures.append("classification")
        for key, expected in case.get("expected", {}).items():
            case_checks += 1
            value = values[key]
            matched = not value.normalized if expected is None else value.normalized == expected and value.status != "conflict"
            if expected is None:
                empty += 1
                empty_matches += matched
            if not matched:
                failures.append(key)
        for key in case.get("conflicts", []):
            case_checks += 1
            conflicts += 1
            matched = values[key].status == "conflict"
            conflict_matches += matched
            if not matched:
                failures.append(key + ":conflict")
        if any(doc.get("format") in ("scan", "scanned_pdf") for doc in case["documents"]):
            case_checks += 1
            if not methods or any(method not in ("ocr", "pdf_ocr") for method in methods):
                failures.append("scan_reading")
        assertions += case_checks
        passed += case_checks - len(failures)
        results.append({"id": case["id"], "assertions": case_checks, "passed": case_checks - len(failures),
                        "failures": failures, "methods": methods, "source_sha256": checksums,
                        "elapsed_seconds": round(time.monotonic() - started, 3)})
    accepted = passed == assertions and conflict_matches == conflicts and empty_matches == empty
    return {"version": 1, "scope": "local rules extraction", "dataset_sha256": hashlib.sha256(encoded).hexdigest(),
            "data_class": dataset.get("data_class", "unspecified"), "cases": len(results),
            "assertions": assertions, "passed": passed, "pass_rate": round(passed / assertions, 4),
            "conflicts": {"expected": conflicts, "detected": conflict_matches},
            "absent_values": {"expected": empty, "correctly_empty": empty_matches},
            "accepted": accepted, "results": results,
            "limits": "Synthetic results are regression evidence, not measured real-office accuracy or calibrated confidence."}
