"""Regression coverage for the presentation review's integrity failures."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cryptography.fernet import InvalidToken
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent, AuditImmutable
from apps.audit.services import record, verify_chain
from apps.connectors.models import Connector, Transfer
from apps.connectors.services import acknowledge, build_payload, export_path, receive_demo, render, transfer
from apps.core import demo_docs, metrics
from apps.core.models import Membership, Tenant
from apps.core.testing import make_office, property_files, property_matter, reset_vault
from apps.intake.api import matter_json
from apps.intake.models import Attachment, IntakeMessage
from apps.matters import services
from apps.matters.engine.aggregate import decide
from apps.matters.engine.providers import AnthropicProvider, RuleProvider
from apps.matters.models import ExtractedField, Matter
from apps.studio.models import FieldDefinition, MatterType


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PilotIntegrityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.office, cls.users = make_office("pilot")
        cls.other, cls.other_users = make_office("other")

    def setUp(self):
        reset_vault()
        self.matter = property_matter(self.office, blatt_contract="812")
        self.notary = Membership.objects.get(user=self.users["notary"], tenant=self.office)
        self.xml = Connector.objects.get(tenant=self.office, kind="xml")

    def approve(self):
        services.confirm_field(self.matter.fields.get(definition__key="purchase_price"), self.users["clerk"])
        services.approve(self.matter, self.notary)
        return self.matter

    def test_stale_review_cannot_undo_approval(self):
        stale = self.matter.fields.select_related("matter", "definition").get(definition__key="buyer_name")
        self.approve()
        with self.assertRaises(ValidationError):
            services.correct_field(stale, self.users["clerk"], "Erika Mustermann")
        self.matter.refresh_from_db()
        self.assertEqual(self.matter.status, "approved")
        self.assertIsNotNone(self.matter.approved_at)
        self.assertEqual(stale.__class__.objects.get(pk=stale.pk).normalized, "Anna Schmidt")

    def test_approved_export_uses_frozen_mapping(self):
        self.approve()
        before = render(self.xml, self.matter)[0]
        FieldDefinition.objects.filter(matter_type=self.matter.matter_type, key="buyer_name").update(destination_path="NewBuyer.Name")
        self.assertEqual(before, render(self.xml, self.matter)[0])
        self.assertEqual(build_payload(self.matter)[0]["Beteiligter"]["Kaeufer"]["Name"], "Anna Schmidt")

    def test_studio_rejects_duplicate_and_prefix_paths(self):
        self.client.force_login(self.users["admin"])
        field = self.matter.fields.get(definition__key="buyer_name").definition
        for path in ("Beteiligter.Verkaeufer.Name", "Beteiligter.Verkaeufer"):
            response = self.client.post(reverse("studio:save_field", args=["property-purchase", field.pk]),
                                        {"destination_path": path, "auto_threshold": "0.9"})
            self.assertEqual(response.status_code, 302)
            field.refresh_from_db()
            self.assertEqual(field.destination_path, "Beteiligter.Kaeufer.Name")

    def test_legacy_approval_requires_reapproval(self):
        self.approve()
        Matter.objects.filter(pk=self.matter.pk).update(approval_enc=b"", approval_hash="")
        with self.assertRaises(ValidationError):
            transfer(self.matter, self.xml, self.users["notary"])
        services.reopen(self.matter, self.notary)
        services.approve(self.matter, self.notary)
        self.assertTrue(self.matter.approval_hash)

    def test_transfer_is_idempotent_and_needs_matching_receipt(self):
        self.approve()
        first = transfer(self.matter, self.xml, self.users["notary"])
        again = transfer(self.matter, self.xml, self.users["notary"])
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(first.status, "exported")
        self.matter.refresh_from_db()
        self.assertEqual(self.matter.status, "approved")
        self.assertIsNone(self.matter.transferred_at)
        with self.assertRaises(ValidationError):
            acknowledge(first, self.users["notary"], checksum="0" * 64, receipt_reference="WRONG")
        acknowledge(first, self.users["notary"], checksum=first.checksum, receipt_reference="DEST-123")
        self.matter.refresh_from_db()
        self.assertEqual(self.matter.status, "transferred")
        self.assertTrue(verify_chain(self.office).ok)

    def test_legacy_export_cannot_skip_frozen_approval_by_acknowledging(self):
        self.approve()
        result = transfer(self.matter, self.xml, self.users["notary"])
        Matter.objects.filter(pk=self.matter.pk).update(approval_enc=b"", approval_hash="")
        Transfer.objects.filter(pk=result.pk).update(approval_hash="")
        with self.assertRaises(ValidationError):
            acknowledge(result, self.users["notary"], checksum=result.checksum, receipt_reference="LEGACY")

    def test_export_payload_cannot_be_replaced_with_a_different_approved_export(self):
        self.approve()
        first = transfer(self.matter, self.xml, self.users["notary"])
        other_matter = property_matter(self.office, blatt_contract="812", buyer="Erika Mustermann", buyer_full="Erika Mustermann")
        services.confirm_field(other_matter.fields.get(definition__key="purchase_price"), self.users["clerk"])
        services.approve(other_matter, self.notary)
        second = transfer(other_matter, self.xml, self.users["notary"])
        Transfer.objects.filter(pk=first.pk).update(payload_enc=second.payload_enc, checksum=second.checksum)
        with self.assertRaises(ValidationError):
            acknowledge(first, self.users["notary"], checksum=second.checksum, receipt_reference="COPIED")

    def test_folder_export_and_demo_receiver_check_actual_file(self):
        self.matter.is_demo = True
        self.matter.save(update_fields=["is_demo"])
        self.xml.delivery = "folder"
        self.xml.save()
        self.approve()
        result = transfer(self.matter, self.xml, self.users["notary"])
        self.assertEqual(result.status, "pending")
        self.assertEqual(hashlib.sha256(export_path(result).read_bytes()).hexdigest(), result.checksum)
        original = export_path(result).read_bytes()
        export_path(result).write_bytes(b"modified")
        with self.assertRaises(ValidationError):
            receive_demo(result, self.users["notary"])
        export_path(result).write_bytes(original)
        with override_settings(LEXFLOW_DEMO_MODE=True):
            self.assertEqual(receive_demo(result, self.users["notary"]).status, "delivered")

    def test_demo_receiver_rejects_live_matter(self):
        self.approve()
        result = transfer(self.matter, self.xml, self.users["notary"])
        with self.assertRaises(ValidationError):
            receive_demo(result, self.users["notary"])

    def test_service_role_and_office_gates(self):
        self.approve()
        for user in (self.users["clerk"], self.other_users["notary"]):
            with self.assertRaises(PermissionDenied):
                transfer(self.matter, self.xml, user)

    def test_case_values_and_audit_metadata_are_encrypted_in_database(self):
        services.correct_field(self.matter.fields.get(definition__key="buyer_name"), self.users["clerk"], "Erika Mustermann")
        tables = [("matters_extractedfield", "normalized"), ("matters_fieldrevision", "new_value"),
                  ("intake_intakemessage", "subject"), ("audit_auditevent", "details")]
        with connection.cursor() as cursor:
            for table, column in tables:
                cursor.execute(f"SELECT {column} FROM {table}")
                raw = [row[0] for row in cursor.fetchall()]
                self.assertTrue(all(not value or value.startswith("lfx:v1:") for value in raw))
                self.assertNotIn("Erika Mustermann", str(raw))
        self.assertTrue(verify_chain(self.office).ok)

    def test_case_cipher_is_bound_to_its_office(self):
        foreign = property_matter(self.other)
        field = ExtractedField.objects.get(matter=self.matter, definition__key="buyer_name")
        field.matter = foreign
        with self.assertRaises(InvalidToken):
            _ = field.value

    def test_stale_settings_save_preserves_signed_audit_head(self):
        stale = Tenant.objects.get(pk=self.office.pk)
        record(self.office, "test.new", "New event")
        stale.retention_days = 90
        stale.save()
        self.assertTrue(verify_chain(self.office).ok)

    def test_audit_bulk_mutations_blocked_and_raw_tail_deletion_detected(self):
        last = record(self.office, "test.tail", "Last event")
        with self.assertRaises(AuditImmutable):
            AuditEvent.objects.filter(pk=last.pk).delete()
        with self.assertRaises(AuditImmutable):
            AuditEvent.objects.filter(pk=last.pk).update(summary="edited")
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM audit_auditevent WHERE id = %s", [last.pk])
        self.assertFalse(verify_chain(self.office).ok)
        with self.assertRaises(ValidationError):
            self.approve()

    def test_purge_preserves_legacy_shared_blob_and_removes_email_body(self):
        active = property_matter(self.office, blatt_contract="812")
        expired_file = self.matter.message.files.first()
        active_file = active.message.files.first()
        original_active_path = self.office.vault_dir / active_file.blob_name
        original_active_path.unlink()
        active_file.blob_name = expired_file.blob_name  # legacy content-addressed duplicate
        active_file.save(update_fields=["blob_name"])
        Matter.objects.filter(pk=self.matter.pk).update(status="transferred", transferred_at=timezone.now() - timedelta(days=400))
        call_command("lexflow_purge", stdout=StringIO())
        self.assertTrue(active_file.read_bytes().startswith(b"%PDF"))
        self.matter.message.refresh_from_db()
        self.assertEqual(self.matter.message.body, "")
        expired_file.refresh_from_db()
        self.assertEqual(expired_file.text_method, "purged")

    def test_source_selection_does_not_learn_client_prefix(self):
        msg = services.ingest_message(self.office, subject="Kauf", files=[("names.txt", "text/plain", "Anna Schmidt: Peter Müller".encode())])
        field = msg.matter.fields.get(definition__key="buyer_name")
        before = list(field.definition.anchors)
        att = msg.files.first()
        start = att.text.index("Peter")
        self.assertIsNone(services.teach_from_selection(msg.matter, self.users["clerk"], att, start, len(att.text), field))
        field.definition.refresh_from_db()
        self.assertEqual(field.definition.anchors, before)
        self.assertFalse(AuditEvent.objects.filter(tenant=self.office, action="studio.anchor_learned").exists())

    def test_same_file_conflict_and_duplicate_upload_confidence(self):
        definition = self.matter.matter_type.fields.get(key="grundbuch")
        doc = SimpleNamespace(display_name="one", sha256="same")
        candidates = RuleProvider().extract(doc, "Grundbuch: München Blatt 812\nGrundbuch: München Blatt 872", [definition])
        self.assertEqual(decide(definition, candidates).status, "conflict")
        candidates = RuleProvider().extract(doc, "Grundbuch München Blatt 812", [definition])
        self.assertEqual(decide(definition, candidates * 3).confidence, candidates[0].confidence)
        self.assertEqual(decide(definition, candidates * 3).status, "review")

    def test_ungrounded_provider_output_cannot_be_aggregated(self):
        definition = self.matter.matter_type.fields.get(key="buyer_name")
        provider = AnthropicProvider(api_key="test", model="test")
        reply = {"content": [{"type": "tool_use", "input": {"buyer_name": {
            "value": "Erika Mustermann", "source_quote": "Erika Mustermann", "confidence": 0.97}}}]}
        with mock.patch.object(provider, "_call_api", return_value=reply):
            candidates = provider.extract(SimpleNamespace(display_name="doc"), "No name here", [definition])
        self.assertEqual(candidates, [])
        self.assertEqual(decide(definition, candidates).status, "missing")

    def test_gmbh_export_has_consistent_nested_schema(self):
        data = dict(office="Demo", company="Isarwerk Software GmbH", seat="München", capital="25.000,00 EUR",
                    director="Lukas Hartmann", director_birth="22.09.1990", director_address="Barer Straße 51, 80799 München",
                    purpose="Entwicklung von Software")
        msg = services.ingest_message(self.office, subject="GmbH Gründung", files=[
            ("formation.pdf", "application/pdf", demo_docs.gruendungsfragebogen(data))])
        tree, _ = build_payload(msg.matter)
        self.assertEqual(tree["Gesellschaft"]["Geschaeftsfuehrer"]["Name"], "Lukas Hartmann")
        self.assertEqual(tree["Gesellschaft"]["Geschaeftsfuehrer"]["Geburtsdatum"], "1990-09-22")

    def test_api_rejects_oversized_unsupported_and_empty_inputs(self):
        token = self.office.issue_api_token()
        with override_settings(LEXFLOW_MAX_UPLOAD_MB=1):
            for upload in (SimpleUploadedFile("big.txt", b"x" * (1024 * 1024 + 1)), SimpleUploadedFile("a.exe", b"bad")):
                self.assertEqual(self.client.post(reverse("api_intake"), {"subject": "x", "files": [upload]},
                                                 HTTP_AUTHORIZATION="Bearer " + token).status_code, 400)
        self.assertEqual(self.client.post(reverse("api_intake"), {"subject": "x"},
                                         HTTP_AUTHORIZATION="Bearer " + token).status_code, 400)

    def test_bad_pdf_creates_no_message_or_orphan(self):
        before_files = set(self.office.vault_dir.iterdir())
        before_count = IntakeMessage.objects.count()
        with self.assertRaises(ValidationError):
            services.ingest_message(self.office, subject="Bad PDF", files=[("bad.pdf", "application/pdf", b"%PDF-broken")])
        self.assertEqual(before_files, set(self.office.vault_dir.iterdir()))
        self.assertEqual(before_count, IntakeMessage.objects.count())

    def test_provider_failure_retains_originals_and_can_retry(self):
        with mock.patch("apps.matters.services.run_extraction", side_effect=RuntimeError("provider unavailable")):
            msg = services.ingest_message(self.office, subject="Kauf retry", files=property_files())
        self.assertEqual(msg.status, "failed")
        self.assertEqual(msg.files.count(), 3)
        self.assertFalse(Matter.objects.filter(message=msg).exists())
        self.assertTrue(msg.files.first().read_bytes())
        self.assertIsNotNone(services.process_message(msg))

    def test_intake_external_id_is_idempotent(self):
        first = services.ingest_message(self.office, subject="Kauf", body="Sample", external_id="stable-id")
        second = services.ingest_message(self.office, subject="Kauf", body="Sample", external_id="stable-id")
        self.assertEqual(first.pk, second.pk)

    def test_api_readiness_includes_policy_review(self):
        data = matter_json(self.matter)
        self.assertTrue(data["blocked"])
        self.assertTrue(data["blockers"])
        self.assertFalse(data["has_conflicts"])

    def test_reextraction_preserves_review_history_and_initial_metrics(self):
        field = self.matter.fields.get(definition__key="buyer_name")
        services.correct_field(field, self.users["clerk"], "Erika Mustermann")
        self.matter.refresh_from_db()
        initial = self.matter.initial_metrics.copy()
        services.run_extraction(self.matter)
        history = self.office.cipher.decrypt_text(self.matter.extraction_history.get().payload_enc)
        self.assertIn("Erika Mustermann", history)
        self.matter.refresh_from_db()
        self.assertEqual(initial, self.matter.initial_metrics)
        self.assertIn("buyer_name", self.matter.corrected_keys)

    def test_unreadable_source_requires_explicit_review(self):
        Attachment.objects.create(tenant=self.office, message=self.matter.message, filename="scan.png", text_method="needs_ocr")
        self.assertTrue(any("unreadable" in item for item in services.approval_blockers(self.matter)))
        self.client.force_login(self.users["clerk"])
        att = self.matter.message.files.order_by("-pk").first()
        self.client.post(reverse("matters:acknowledge_source", args=[self.matter.ref, att.pk]), {"reviewed": "yes"})
        self.assertFalse(any("unreadable" in item for item in services.approval_blockers(self.matter)))

    def test_dedicated_deployment_limits_office_and_tokens(self):
        other_token = self.other.issue_api_token()
        with override_settings(LEXFLOW_DEPLOYMENT="dedicated", LEXFLOW_TENANT_SLUG="pilot"):
            with self.assertRaises(ValidationError):
                Tenant.objects.create(slug="third", name="Third")
            self.assertEqual(self.client.get(reverse("api_matter", args=[self.matter.ref]),
                                             HTTP_AUTHORIZATION="Bearer " + other_token).status_code, 401)

    def test_demo_seeding_and_presenter_route_are_disabled_in_live_mode(self):
        with override_settings(LEXFLOW_DEMO_MODE=False):
            from django.core.management.base import CommandError
            with self.assertRaises(CommandError):
                call_command("seed_lexflow", stdout=StringIO())
            self.client.force_login(self.users["clerk"])
            self.assertEqual(self.client.get(reverse("core:presentation")).status_code, 404)

    def test_production_configuration_rejects_known_development_keys(self):
        env = dict(os.environ, DJANGO_DEBUG="0")
        for name in ("DJANGO_SECRET_KEY", "LEXFLOW_MASTER_KEY"):
            env.pop(name, None)
        result = subprocess.run([sys.executable, "-c", "import config.settings"], cwd=settings.BASE_DIR,
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY", result.stderr)
