"""The LexFlow pipeline: receive → classify → extract → validate → review → transfer → audit."""
import hashlib
import json
import re
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.services import digest, record
from apps.core.models import Membership, Tenant
from apps.intake.validation import validate_intake
from apps.intake.models import Attachment, IntakeMessage
from apps.studio.models import DocumentType, MatterType

from .engine.aggregate import decide
from .engine.classify import classify_document, classify_matter
from .engine.normalize import normalize, normalize_value
from .engine.providers import get_provider
from .engine.text import extract_text
from .models import ExtractedField, ExtractionSnapshot, FieldCandidate, FieldRevision, Matter

# --------------------------------------------------------------------- receive


@transaction.atomic
def ingest_message(tenant, *, subject, body="", files=(), source=IntakeMessage.Source.EMAIL,
                   sender_name="", sender_email="", received_at=None, external_id="", actor=None, process=True,
                   is_demo=False):
    """Validate before storing; retain retryable processing failures without orphan blobs."""
    files = list(files)
    validate_intake(subject, body, files)
    Tenant.objects.select_for_update().get(pk=tenant.pk)
    if external_id:
        existing = IntakeMessage.objects.filter(tenant=tenant, external_id=external_id).first()
        if existing:
            return existing
    prepared = []
    try:
        for filename, content_type, data in files:
            prepared.append((filename, content_type, data, extract_text(filename, content_type or "", data)))
    except Exception as exc:
        raise ValidationError("A document could not be read. Check that PDFs are valid and unlocked.") from exc
    message = IntakeMessage(
        tenant=tenant, source=source, sender_name=sender_name[:120], sender_email=sender_email[:254],
        subject=subject[:255], received_at=received_at or timezone.now(), external_id=external_id,
        is_demo=is_demo,
    )
    message.body = body
    message.save()
    created_blobs = []
    try:
        if body.strip():
            att = Attachment(tenant=tenant, message=message, kind=Attachment.Kind.BODY, filename="Email text",
                             content_type="text/plain", text_method="plain", page_count=1)
            att.text, att.size = body, len(body.encode())
            att.save()
        for filename, content_type, data, (text, method, pages) in prepared:
            att = Attachment(tenant=tenant, message=message, filename=filename[:200], content_type=content_type or "")
            att.store_bytes(data)
            created_blobs.append(tenant.vault_dir / att.blob_name)
            att.text, att.text_method, att.page_count = text, method, pages
            att.save()
        record(tenant, "intake.received", f"Received {message.get_source_display().lower()} with {len(files)} file(s)",
               actor=actor, obj=message, details={"sha256": [hashlib.sha256(f[2]).hexdigest() for f in files]})
        if process:
            try:
                process_message(message, actor=actor)
            except Exception:
                # The processing savepoint rolls back; originals remain available for retry.
                message.refresh_from_db()
                message.status = IntakeMessage.Status.FAILED
                message.error = "Processing failed. Review the source documents and retry."
                message.save(update_fields=["status", "error"])
                record(tenant, "intake.failed", "Processing failed; originals retained for retry", actor=actor, obj=message)
        message.refresh_from_db()
        return message
    except Exception:
        for path in created_blobs:
            path.unlink(missing_ok=True)
        raise


# -------------------------------------------------------------------- classify


@transaction.atomic
def process_message(message, actor=None):
    Tenant.objects.select_for_update().get(pk=message.tenant_id)
    message = IntakeMessage.objects.select_for_update().get(pk=message.pk)
    existing = Matter.objects.filter(message=message).first()
    if existing:
        return existing
    tenant = message.tenant
    attachments = list(message.attachments.all())
    doc_types = list(DocumentType.objects.filter(tenant=tenant))
    matter_types = list(MatterType.objects.filter(tenant=tenant, is_active=True))

    decisions = []
    for att in attachments:
        d = classify_document(att.text, doc_types)
        att.doc_type = d.doc_type
        att.doc_type_confidence = d.confidence
        att.save(update_fields=["doc_type", "doc_type_confidence"])
        decisions.append(d)

    md = classify_matter(message.subject, decisions, matter_types)
    matter, _ = Matter.objects.get_or_create(
        message=message, defaults={"tenant": tenant, "title": message.subject, "created_at": message.received_at, "is_demo": message.is_demo}
    )
    matter.matter_type = md.matter_type
    matter.type_confidence = md.confidence
    matter.save()
    record(
        tenant, "matter.classified",
        f"Classified as {md.matter_type.name if md.matter_type else 'unknown'}",
        actor=actor, obj=matter,
        details={
            "matter_type": md.matter_type.key if md.matter_type else None,
            "confidence": str(md.confidence),
            "documents": [
                {"file": a.display_name, "type": a.doc_type.key if a.doc_type else None, "phrases": d.hits}
                for a, d in zip(attachments, decisions)
            ],
        },
    )
    message.status = IntakeMessage.Status.PROCESSED
    message.processed_at = timezone.now()
    message.save(update_fields=["status", "processed_at"])
    run_extraction(matter, actor=actor)
    return matter


# --------------------------------------------------------- extract + validate


@transaction.atomic
def run_extraction(matter, actor=None):
    _lock_matter(matter)
    tenant = matter.tenant
    if matter.fields.exists():
        history = [{"key": f.definition.key, "value": f.value, "normalized": f.normalized,
                    "status": f.status, "revisions": [{"old": r.old_value, "new": r.new_value,
                    "action": r.action, "at": r.at.isoformat()} for r in f.revisions.all()]}
                   for f in matter.fields.select_related("definition").prefetch_related("revisions")]
        ExtractionSnapshot.objects.create(matter=matter, payload_enc=tenant.cipher.encrypt_text(json.dumps(history)))
    matter.fields.all().delete()
    if matter.matter_type is None:
        matter.status = Matter.Status.UNCLASSIFIED
        matter.save(update_fields=["status"])
        return matter

    provider = get_provider(tenant)
    if settings.LEXFLOW_AI_PROVIDER == "anthropic" and provider.name != "anthropic":
        record(tenant, "policy.external_ai_blocked",
               "External AI not permitted for this office — local rules engine used", obj=matter)

    definitions = list(matter.matter_type.fields.all())
    by_field = defaultdict(list)
    for att in matter.message.attachments.all():
        if not att.text:
            continue
        for cand in provider.extract(att, att.text, definitions):
            by_field[cand.field_key].append(cand)

    tally = defaultdict(int)
    for fdef in definitions:
        dec = decide(fdef, by_field.get(fdef.key, []))
        best = dec.best
        ef = ExtractedField.objects.create(
            matter=matter, definition=fdef, value=dec.value[:500], normalized=dec.normalized[:500],
            confidence=dec.confidence, status=dec.status, initial_status=dec.status,
            source=best.attachment if best else None,
            span_start=best.span_start if best else None, span_end=best.span_end if best else None,
            method=best.method if best else "", note=dec.note[:255],
        )
        for c in dec.candidates:
            FieldCandidate.objects.create(
                field=ef, value=c.value[:500], normalized=c.normalized[:500], confidence=c.confidence,
                source=c.attachment, span_start=c.span_start, span_end=c.span_end, method=c.method,
                anchor=c.anchor[:80],
            )
        tally[dec.status] += 1
        if dec.status == "conflict":
            record(tenant, "field.conflict_detected", f"Conflict on {fdef.label} — transfer blocked", actor=None,
                   obj=matter, details={"field": fdef.key, "variants": len({c.normalized for c in dec.candidates})})

    matter.provider = provider.label
    if not matter.initial_metrics:
        matter.initial_metrics = {"total": len(definitions), "found": sum(bool(f.value) for f in matter.fields.all()),
                                  "auto": sum(bool(f.value) and f.status == "auto" for f in matter.fields.all()),
                                  "low": tally["missing"] + tally["conflict"] +
                                  sum(f.status == "review" and not f.definition.always_review
                                      for f in matter.fields.select_related("definition"))}
    matter.save(update_fields=["provider", "initial_metrics"])
    refresh_status(matter)
    record(
        tenant, "matter.extracted",
        f"Extracted {len(definitions) - tally['missing']} of {len(definitions)} fields with {provider.label}",
        actor=actor, obj=matter, details={"provider": provider.name, **dict(tally)},
    )
    return matter


def refresh_status(matter):
    if matter.is_locked:
        return matter
    if matter.matter_type is None:
        matter.status = Matter.Status.UNCLASSIFIED
    elif matter.fields.filter(status__in=ExtractedField.OPEN).exists():
        matter.status = Matter.Status.NEEDS_REVIEW
    else:
        matter.status = Matter.Status.READY
    matter.save(update_fields=["status"])
    return matter


# ---------------------------------------------------------------------- review


def _guard(matter):
    if matter.is_locked:
        raise ValidationError("This matter is approved and locked.")


def _lock_matter(matter):
    # All mutation paths take locks in this order, including audit/config writes.
    Tenant.objects.select_for_update().get(pk=matter.tenant_id)
    Matter.objects.select_for_update().get(pk=matter.pk)
    matter.refresh_from_db()
    _guard(matter)
    return matter


def _lock_field(field, user):
    matter = _lock_matter(field.matter)
    if not Membership.objects.filter(tenant=matter.tenant, user=user).exists():
        raise PermissionDenied("Office membership is required.")
    field.refresh_from_db()
    field.matter = matter
    return matter


def _revise(field, user, new_value, action):
    matter = field.matter
    if new_value != (field.normalized or field.value) and field.definition.key not in matter.corrected_keys:
        matter.corrected_keys = matter.corrected_keys + [field.definition.key]
        matter.save(update_fields=["corrected_keys"])
    FieldRevision.objects.create(field=field, old_value=field.normalized or field.value, new_value=new_value,
                                 action=action, user=user)


@transaction.atomic
def confirm_field(field, user):
    _lock_field(field, user)
    if field.status == ExtractedField.Status.CONFLICT:
        raise ValidationError("Choose which document is correct before confirming.")
    if not (field.normalized or field.value):
        raise ValidationError("Enter a value before confirming.")
    _revise(field, user, field.normalized or field.value, "confirmed")
    field.status = ExtractedField.Status.CONFIRMED
    field.reviewed_by, field.reviewed_at = user, timezone.now()
    field.save()
    record(field.matter.tenant, "field.confirmed", f"Confirmed {field.definition.label}", actor=user,
           obj=field.matter, details={"field": field.definition.key, "value_digest": digest(field.normalized)})
    return refresh_status(field.matter)


@transaction.atomic
def correct_field(field, user, raw_value, *, source=None, span=None, action="corrected"):
    _lock_field(field, user)
    raw_value = (raw_value or "").strip()
    if not raw_value:
        raise ValidationError("A corrected value cannot be empty.")
    parsed = normalize(field.definition.data_type, raw_value)
    if parsed is None and field.definition.data_type in ("money", "date", "grundbuch"):
        raise ValidationError(f"“{raw_value}” is not a valid {field.definition.get_data_type_display().lower()}.")
    normalized = parsed[2] if parsed else normalize_value(field.definition.data_type, raw_value)
    old = field.normalized or field.value
    _revise(field, user, normalized, action)
    was_conflict = field.status == ExtractedField.Status.CONFLICT
    field.value, field.normalized = raw_value[:500], normalized[:500]
    field.status = ExtractedField.Status.CORRECTED
    field.method = "human"
    field.note = "Corrected by a reviewer."
    field.confidence = Decimal("1.0000")
    if source is not None:
        if source.message_id != field.matter.message_id:
            raise ValidationError("The source belongs to a different matter.")
        field.source = source
        field.span_start, field.span_end = span
    else:
        field.source, field.span_start, field.span_end = None, None, None
    field.reviewed_by, field.reviewed_at = user, timezone.now()
    field.save()
    record(
        field.matter.tenant, "field.conflict_resolved" if was_conflict else "field.corrected",
        f"{'Resolved conflict on' if was_conflict else 'Corrected'} {field.definition.label}", actor=user,
        obj=field.matter,
        details={"field": field.definition.key, "from_digest": digest(old), "to_digest": digest(normalized)},
    )
    return refresh_status(field.matter)


@transaction.atomic
def choose_candidate(field, user, candidate):
    _lock_field(field, user)
    if candidate.field_id != field.pk:
        raise ValidationError("That value does not belong to this field.")
    _revise(field, user, candidate.normalized, "chose_source")
    field.value, field.normalized = candidate.value, candidate.normalized
    field.source, field.span_start, field.span_end = candidate.source, candidate.span_start, candidate.span_end
    field.status = ExtractedField.Status.CONFIRMED
    field.confidence = Decimal("1.0000")
    field.note = f"Reviewer chose {candidate.source.display_name}."
    field.reviewed_by, field.reviewed_at = user, timezone.now()
    field.save()
    record(field.matter.tenant, "field.conflict_resolved", f"Resolved conflict on {field.definition.label}",
           actor=user, obj=field.matter,
           details={"field": field.definition.key, "chosen_document": candidate.source.display_name,
                    "value_digest": digest(candidate.normalized)})
    return refresh_status(field.matter)


LABEL_RE = re.compile(r"[^\W\d_][\w .\-/()äöüÄÖÜß]{1,38}$")


@transaction.atomic
def teach_from_selection(matter, user, attachment, start, end, field):
    """Highlight-to-teach: the selection fills the field; the label before it becomes a new anchor.

    Only the *label* (e.g. "Erwerber") is learned — never the value — so the
    office's workflow improves without retaining any client data in the rules.
    """
    _lock_matter(matter)
    if attachment.message_id != matter.message_id or field.matter_id != matter.pk:
        raise ValidationError("Select a field and document from this matter.")
    text = attachment.text
    if not (0 <= start < end <= len(text)):
        raise ValidationError("The selection is outside the document.")
    # Selection fills case data only. Reusable schema labels are explicitly
    # configured by an administrator in the studio, never inferred from a person.
    correct_field(field, user, text[start:end].strip(), source=attachment, span=(start, end), action="taught")
    return None


def approval_blockers(matter):
    blockers = []
    if matter.matter_type is None:
        blockers.append("Choose the matter type.")
        return blockers
    if matter.message.status != IntakeMessage.Status.PROCESSED:
        blockers.append("Finish processing the intake.")
    if matter.message.attachments.filter(text_method__in=["needs_ocr", "ocr", "pdf_ocr", "ocr_partial"],
                                         reviewed_at__isnull=True).exists():
        blockers.append("Review and acknowledge every scanned, unreadable or partially readable original document.")
    definitions = list(matter.matter_type.fields.all())
    if matter.fields.count() != len(definitions):
        blockers.append("Extract all configured fields before approval.")
    from apps.studio.validation import validate_paths
    try:
        validate_paths(f.destination_path for f in definitions)
        from .case_data import validate_details
        validate_details(matter)
    except ValidationError as exc:
        blockers.extend(exc.messages)
    for f in matter.fields.select_related("definition"):
        if f.definition.required and not (f.normalized or f.value):
            blockers.append(f"Enter {f.definition.label} — it is required.")
        elif f.definition.always_review and f.status not in ("confirmed", "corrected") and f.value:
            blockers.append(f"Confirm {f.definition.label} under the current office policy.")
        elif f.status == ExtractedField.Status.CONFLICT:
            blockers.append(f"Resolve the conflict on {f.definition.label}.")
        elif f.status == ExtractedField.Status.MISSING:
            blockers.append(f"Enter {f.definition.label} — it is required.")
        elif f.status == ExtractedField.Status.REVIEW:
            blockers.append(f"Check {f.definition.label}.")
    return blockers


@transaction.atomic
def approve(matter, membership):
    if membership.tenant_id != matter.tenant_id or not membership.can_approve:
        raise PermissionDenied("Only a notary can approve a matter for transfer.")
    _lock_matter(matter)
    blockers = approval_blockers(matter)
    from apps.audit.services import verify_chain
    if not verify_chain(matter.tenant).ok:
        blockers.append("Resolve the audit integrity failure before approving.")
    if blockers:
        raise ValidationError(blockers)
    matter.status = Matter.Status.APPROVED
    matter.approved_at = timezone.now()
    matter.approved_by = membership.user
    from apps.connectors.services import make_approval_snapshot
    snapshot = make_approval_snapshot(matter)
    encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    matter.approval_enc = matter.tenant.cipher.encrypt_text(encoded)
    matter.approval_hash = hashlib.sha256(encoded.encode()).hexdigest()
    matter.save(update_fields=["status", "approved_at", "approved_by", "approval_enc", "approval_hash"])
    record(matter.tenant, "matter.approved", "Approved for transfer", actor=membership.user, obj=matter,
           details={"fields": matter.fields.count(),
                    "approval": matter.approval_hash,
                    "corrected": matter.fields.filter(status=ExtractedField.Status.CORRECTED).count()})
    return matter


@transaction.atomic
def set_matter_type(matter, user, matter_type):
    _lock_matter(matter)
    if matter_type.tenant_id != matter.tenant_id:
        raise PermissionDenied
    if matter.matter_type_id != matter_type.pk and any(matter.case_data.get(g) for g in ("participants", "properties")):
        raise ValidationError("Remove saved participant and property details before changing the workflow.")
    matter.matter_type = matter_type
    matter.type_confidence = Decimal("1.0000")
    matter.save()
    record(matter.tenant, "matter.reclassified", f"Set matter type to {matter_type.name}", actor=user, obj=matter,
           details={"matter_type": matter_type.key})
    return run_extraction(matter, actor=user)


@transaction.atomic
def reopen(matter, membership):
    if membership.tenant_id != matter.tenant_id or not membership.can_approve:
        raise PermissionDenied("A notary must reopen the matter.")
    Tenant.objects.select_for_update().get(pk=matter.tenant_id)
    Matter.objects.select_for_update().get(pk=matter.pk)
    matter.refresh_from_db()
    if matter.status == Matter.Status.TRANSFERRED:
        raise ValidationError("A delivered matter is immutable. Start a new intake for an amendment.")
    matter.status = Matter.Status.NEEDS_REVIEW
    matter.approval_enc, matter.approval_hash = b"", ""
    matter.approved_at, matter.approved_by = None, None
    matter.save(update_fields=["status", "approval_enc", "approval_hash", "approved_at", "approved_by"])
    record(matter.tenant, "matter.reopened", "Reopened for a new review and approval", actor=membership.user, obj=matter)
    return refresh_status(matter)
