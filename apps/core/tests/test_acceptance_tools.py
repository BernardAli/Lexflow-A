from email.message import EmailMessage
import json
from pathlib import Path
import tempfile
from unittest import mock

from django.test import TestCase, override_settings
from django.core.exceptions import ValidationError
from django.db import connection

from apps.core.evaluation import evaluate
from apps.core.testing import make_office
from tools import scanner_agent
from apps.audit.checkpoints import export_checkpoint, verify_checkpoint
from apps.audit.services import record, verify_chain
from apps.core.models import Tenant


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AcceptanceToolTests(TestCase):
    def setUp(self):
        self.office, _ = make_office()
        self.temp = tempfile.TemporaryDirectory(prefix="lexflow-tool-test-")
        self.addCleanup(self.temp.cleanup)

    def test_eml_preserves_decoded_body_sender_and_supported_attachments(self):
        message = EmailMessage()
        message["From"] = "Demo Office <demo@example.invalid>"
        message["Subject"] = "Kaufvertrag Muster"
        message.set_content("Käufer: Maria Beispiel")
        message.add_attachment(b"synthetic source", maintype="application", subtype="pdf", filename="sample.pdf")
        path = Path(self.temp.name) / "message.eml"
        path.write_bytes(message.as_bytes())
        fields, files = scanner_agent.intake(path)
        self.assertIn("Käufer: Maria Beispiel", fields["body"])
        self.assertEqual(fields["sender_email"], "demo@example.invalid")
        self.assertEqual(files, [("sample.pdf", "application/pdf", b"synthetic source")])
        self.assertEqual(scanner_agent.intake(path)[0]["external_id"], fields["external_id"])

    def test_agent_rejects_header_injection_and_remote_plaintext(self):
        with self.assertRaises(ValueError):
            scanner_agent.multipart({}, [('bad"\r\n.txt', "text/plain", b"data")])
        with mock.patch.object(scanner_agent.http.client, "HTTPConnection") as connection:
            with self.assertRaises(ValueError):
                scanner_agent.send("http://example.invalid", "lfx_example", {}, [])
            connection.assert_not_called()

    def test_evaluation_reports_errors_without_leaking_actual_client_values(self):
        path = Path(self.temp.name) / "dataset.json"
        path.write_text(json.dumps({"version": 1, "data_class": "synthetic", "cases": [{"id": "deliberate-failure",
            "subject": "Kaufvertrag", "matter_type": "property-purchase", "documents": [{"text": "Käufer: Maria Beispiel"}],
            "expected": {"buyer_name": "Different Value"}}]}))
        result = evaluate(self.office, path)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["results"][0]["failures"], ["buyer_name"])
        self.assertNotIn("Maria Beispiel", json.dumps(result))
        self.assertNotIn("Different Value", json.dumps(result))

    def test_external_checkpoint_detects_replay_of_an_entire_older_valid_database(self):
        record(self.office, "test.first", "Earlier state")
        self.office.refresh_from_db()
        previous = {name: getattr(self.office, name) for name in ("audit_seq", "audit_head", "audit_seal")}
        last = record(self.office, "test.next", "Later state")
        path = Path(self.temp.name) / "external.json"
        export_checkpoint(self.office, path)
        self.assertTrue(verify_checkpoint(self.office, path)["verified"])
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM audit_auditevent WHERE id = %s", [last.pk])
        Tenant.objects.filter(pk=self.office.pk).update(**previous)
        self.assertTrue(verify_chain(self.office).ok)  # An internally valid old DB.
        with self.assertRaises(ValidationError):
            verify_checkpoint(self.office, path)

    def test_external_checkpoint_accepts_later_events_and_rejects_editing(self):
        record(self.office, "test.first", "First event")
        path = Path(self.temp.name) / "external.json"
        export_checkpoint(self.office, path)
        record(self.office, "test.next", "Next event")
        self.assertTrue(verify_checkpoint(self.office, path)["verified"])
        with self.assertRaises(ValidationError):
            export_checkpoint(self.office, path)
        value = json.loads(path.read_text())
        value["seq"] += 1
        path.write_text(json.dumps(value))
        with self.assertRaises(ValidationError):
            verify_checkpoint(self.office, path)
