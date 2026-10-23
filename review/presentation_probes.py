"""Readiness probes using an in-memory database and temporary encrypted vault.

Run from the project root with its virtualenv Python. No existing office data
or vault files are read or modified. All document values below are synthetic.
"""
import hashlib
import json
import logging
import os
import sys
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path.cwd()))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
from django.conf import settings

settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.audit.services import record, verify_chain
from apps.connectors.models import Connector
from apps.connectors.services import build_payload, render, transfer
from apps.core import demo_docs
from apps.core.models import Membership
from apps.core.testing import make_office, property_matter
from apps.intake.api import matter_json
from apps.intake.models import Attachment, IntakeMessage
from apps.matters import services
from apps.matters.engine.aggregate import decide
from apps.matters.engine.providers import AnthropicProvider, RuleProvider
from apps.matters.models import ExtractedField, Matter
from apps.studio.models import FieldDefinition, MatterType

logging.disable(logging.CRITICAL)
results = []


def emit(name, **details):
    row = {"probe": name, **details}
    results.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)


def ready(tenant, users):
    matter = property_matter(tenant, blatt_contract="812")
    services.confirm_field(matter.fields.get(definition__key="purchase_price"), users["clerk"])
    matter.refresh_from_db()
    assert not services.approval_blockers(matter)
    return matter


with tempfile.TemporaryDirectory(prefix="lexflow-review-") as vault:
    settings.LEXFLOW_VAULT_ROOT = Path(vault)
    call_command("migrate", verbosity=0, stdout=StringIO())
    assert connection.settings_dict["NAME"] == ":memory:"

    # Approved output is not bound to a version of the workflow mapping.
    tenant, users = make_office("snapshot")
    matter = ready(tenant, users)
    membership = Membership.objects.get(tenant=tenant, user=users["notary"])
    services.approve(matter, membership)
    xml = Connector.objects.get(tenant=tenant, kind="xml")
    before = render(xml, matter)[0]
    definition = matter.fields.get(definition__key="buyer_name").definition
    client = Client()
    client.force_login(users["admin"])
    response = client.post(reverse("studio:save_field", args=["property-purchase", definition.pk]), {
        "label": definition.label, "destination_path": "Beteiligter.Verkaeufer.Name",
        "auto_threshold": "0.90", "required": "on",
    })
    after = render(xml, matter)[0]
    matter.refresh_from_db()
    tree, flat = build_payload(matter)
    emit("approved_mapping_mutates", admin_http_status=response.status_code,
         approved=matter.status == "approved", payload_changed=before != after,
         buyer_name_missing="Kaeufer" not in tree["Beteiligter"] or "Name" not in tree["Beteiligter"].get("Kaeufer", {}),
         duplicate_destination_count=sum(p == "Beteiligter.Verkaeufer.Name" for p, _, _ in flat))

    # Cached matter state can authorize an edit after another request approved it.
    tenant, users = make_office("stale")
    matter = ready(tenant, users)
    stale_field = ExtractedField.objects.select_related("matter", "definition").get(
        matter=matter, definition__key="buyer_name")
    services.approve(matter, Membership.objects.get(tenant=tenant, user=users["notary"]))
    services.correct_field(stale_field, users["clerk"], "Erika Mustermann")
    matter.refresh_from_db()
    stale_field.refresh_from_db()
    emit("stale_edit_after_approval", new_value=stale_field.normalized,
         resulting_status=matter.status, approved_at_erased=matter.approved_at is None,
         approval_audit_still_present=AuditEvent.objects.filter(tenant=tenant, action="matter.approved").exists())

    # Plaintext derived case fields remain in database columns.
    with connection.cursor() as cursor:
        cursor.execute("SELECT value, normalized FROM matters_extractedfield WHERE id = %s", [stale_field.pk])
        row = cursor.fetchone()
    emit("plaintext_structured_data", raw_database_value=row[0], raw_database_normalized=row[1])

    # Purging one message deletes a hash-named blob also used by an active case.
    tenant, users = make_office("purge")
    shared = ("shared.txt", "text/plain", b"Kaeuferfragebogen: synthetic shared document")
    old = services.ingest_message(tenant, subject="Kauf old", body="Synthetic confidential email body", files=[shared])
    active = services.ingest_message(tenant, subject="Kauf active", files=[shared])
    old_matter = old.matter
    old_matter.status = Matter.Status.TRANSFERRED
    old_matter.transferred_at = timezone.now() - timedelta(days=400)
    old_matter.save()
    old_att, active_att = old.files.first(), active.files.first()
    same_blob = old_att.blob_name == active_att.blob_name
    call_command("lexflow_purge", stdout=StringIO())
    old.refresh_from_db()
    old_att.refresh_from_db()
    try:
        active_att.read_bytes()
        live_error = None
    except FileNotFoundError as exc:
        live_error = type(exc).__name__
    emit("retention_breaks_shared_blob_and_keeps_body", shared_blob=same_blob,
         old_attachment_purged=old_att.text_method == "purged", active_download_error=live_error,
         original_email_body_retained=old.body == "Synthetic confidential email body")

    # A syntactically valid prefix may itself be confidential client content.
    tenant, users = make_office("teach")
    message = services.ingest_message(tenant, subject="Kauf teaching", files=[
        ("names.txt", "text/plain", "Anna Schmidt: Peter Müller".encode())])
    matter = message.matter
    attachment = message.files.first()
    field = matter.fields.get(definition__key="buyer_name")
    start = attachment.text.index("Peter")
    learned = services.teach_from_selection(matter, users["clerk"], attachment, start, len(attachment.text), field)
    field.definition.refresh_from_db()
    emit("learning_persists_personal_prefix", learned=learned,
         client_name_in_rules="Anna Schmidt" in field.definition.anchors,
         client_name_in_audit=AuditEvent.objects.filter(tenant=tenant, details__label="Anna Schmidt").exists())

    # Quotes absent from every document can nevertheless aggregate into AUTO.
    definition = MatterType.objects.get(tenant=tenant, key="property-purchase").fields.get(key="buyer_name")
    provider = AnthropicProvider(api_key="synthetic-test-key", model="synthetic-model")
    reply = {"content": [{"type": "tool_use", "input": {"buyer_name": {
        "value": "Erika Mustermann", "source_quote": "Erika Mustermann", "confidence": 0.97}}}]}
    candidates = []
    with mock.patch.object(provider, "_call_api", return_value=reply):
        for i in range(3):
            attachment = SimpleNamespace(display_name=f"absent-{i}.txt")
            candidates.extend(provider.extract(attachment, "No legal value is present here.", [definition]))
    decision = decide(definition, candidates)
    emit("ungrounded_llm_auto_prefill", candidates=len(candidates), status=decision.status,
         confidence=str(decision.confidence), all_source_spans_empty=all(c.span_start == c.span_end for c in candidates))

    # The rule provider returns the first match and ignores disagreement in a file.
    definition = MatterType.objects.get(tenant=tenant, key="property-purchase").fields.get(key="grundbuch")
    attachment = SimpleNamespace(display_name="conflict.txt")
    candidates = RuleProvider().extract(attachment, "Grundbuch: München Blatt 812\nGrundbuch: München Blatt 872", [definition])
    decision = decide(definition, candidates)
    emit("same_document_conflict_ignored", candidates=len(candidates), normalized=decision.normalized,
         status=decision.status, confidence=str(decision.confidence))

    # Identical uploads are counted as independent evidence.
    document = "Grundbuch München Blatt 812".encode()
    message = services.ingest_message(tenant, subject="Kauf duplicated evidence", files=[
        ("one.txt", "text/plain", document), ("two.txt", "text/plain", document)])
    field = message.matter.fields.get(definition__key="grundbuch")
    emit("duplicate_document_inflates_confidence", unique_document_hashes=len(set(message.files.values_list("sha256", flat=True))),
         candidate_count=field.candidates.count(), status=field.status, confidence=str(field.confidence))

    # The default GmbH mapping nests a birth date beneath a string director name.
    tenant, users = make_office("gmbh")
    data = dict(office="Synthetic Notariat", company="Isarwerk Software GmbH", seat="München",
                capital="25.000,00 EUR", capital_hr="25.000,00 EUR", purpose="Entwicklung und Vertrieb von Software",
                director="Lukas Hartmann", director_birth="22.09.1990", director_address="Barer Straße 51, 80799 München")
    message = services.ingest_message(tenant, subject="GmbH Gründung", files=[
        ("formation.pdf", "application/pdf", demo_docs.gruendungsfragebogen(data)),
        ("register.pdf", "application/pdf", demo_docs.hr_anmeldung(data))])
    matter = message.matter
    try:
        build_payload(matter)
        export_error = None
    except TypeError as exc:
        export_error = str(exc)
    emit("gmbh_default_mapping_crashes", matter_type=matter.matter_type.key,
         director_present=bool(matter.fields.get(definition__key="managing_director").normalized),
         birthdate_present=bool(matter.fields.get(definition__key="director_birthdate").normalized), error=export_error)

    # The public API omits the upload size check used by the web form.
    token = tenant.issue_api_token()
    with override_settings(LEXFLOW_MAX_UPLOAD_MB=1):
        upload = SimpleUploadedFile("large.txt", b"x" * (1024 * 1024 + 1), content_type="text/plain")
        response = Client().post(reverse("api_intake"), {"subject": "Size probe", "files": [upload]},
                                 HTTP_AUTHORIZATION=f"Bearer {token}")
    emit("api_upload_limit_bypassed", configured_limit_bytes=1024 * 1024,
         upload_bytes=1024 * 1024 + 1, http_status=response.status_code)

    # Bad PDFs leave encrypted files behind despite the database rollback.
    files_before = set(Path(vault).rglob("*.lfx"))
    messages_before = IntakeMessage.objects.count()
    try:
        services.ingest_message(tenant, subject="Corrupt PDF", files=[("broken.pdf", "application/pdf", b"%PDF-broken synthetic probe")])
        pdf_error = None
    except Exception as exc:
        pdf_error = type(exc).__name__
    emit("corrupt_pdf_leaves_orphan", exception=pdf_error,
         messages_created=IntakeMessage.objects.count() - messages_before,
         orphan_blobs=len(set(Path(vault).rglob("*.lfx")) - files_before))

    # Truncating the tail is not detectable without an independently saved head.
    tenant, _ = make_office("tail")
    record(tenant, "probe.one", "First synthetic event")
    last = record(tenant, "probe.two", "Second synthetic event")
    deleted = AuditEvent.objects.filter(pk=last.pk).delete()[0]
    report = verify_chain(tenant)
    emit("audit_tail_deletion_undetected", queryset_deleted=deleted, verifies=report.ok, events_remaining=report.checked)

    # API blocked is false although policy-review and required fields block approval.
    tenant, users = make_office("api-blocked")
    matter = property_matter(tenant, blatt_contract="812")
    emit("api_blocked_omits_review_gate", api_blocked=matter_json(matter)["blocked"],
         approval_blockers=services.approval_blockers(matter))

    # Export generation alone marks the transfer delivered.
    matter = ready(tenant, users)
    services.approve(matter, Membership.objects.get(tenant=tenant, user=users["notary"]))
    connector = Connector.objects.get(tenant=tenant, kind="json")
    result = transfer(matter, connector, users["notary"])
    emit("transfer_delivered_without_transport", status=result.status,
         configured_destination_url=hasattr(connector, "url"), stored_payload_bytes=len(result.payload.encode()))

print(json.dumps({"summary": {"probes_completed": len(results), "database": ":memory:", "vault": "temporary"}}), flush=True)
