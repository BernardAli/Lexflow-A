"""Real HTTP contract checks against a separate reference destination."""
import hashlib
import json
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings

from apps.audit.services import verify_chain
from apps.connectors import protocol, services as handoff
from apps.connectors.models import Connector
from apps.core.models import Membership
from apps.core.testing import make_office, property_matter, reset_vault
from apps.matters import services
from integrations.reference_receiver import server


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class HTTPHandoffTests(TestCase):
    def setUp(self):
        reset_vault()
        self.temp = tempfile.TemporaryDirectory(prefix="lexflow-receiver-test-")
        self.addCleanup(self.temp.cleanup)
        self.secret = "separate-reference-secret-" + "a" * 40
        self.database = self.temp.name + "/receiver.sqlite3"
        self.receiver = server(self.database, self.secret)
        thread = threading.Thread(target=self.receiver.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.receiver.server_close)
        self.addCleanup(self.receiver.shutdown)
        self.office, self.users = make_office()
        self.matter = property_matter(self.office, blatt_contract="812")
        self.matter.is_demo = True
        self.matter.save(update_fields=["is_demo"])
        services.confirm_field(self.matter.fields.get(definition__key="purchase_price"), self.users["clerk"])
        services.approve(self.matter, Membership.objects.get(tenant=self.office, user=self.users["notary"]))
        self.connector = Connector.objects.get(tenant=self.office, kind="json")
        self.connector.delivery, self.connector.endpoint_key, self.connector.signing_secret = "webhook", "reference", self.secret
        self.connector.save()
        settings = override_settings(LEXFLOW_WEBHOOK_ALLOW_LOOPBACK=True,
            LEXFLOW_WEBHOOK_ENDPOINTS={"reference": f"http://127.0.0.1:{self.receiver.server_port}/v1/handoffs"})
        settings.enable()
        self.addCleanup(settings.disable)
        self.export = handoff.transfer(self.matter, self.connector, self.users["notary"])

    def test_real_http_receipt_binds_approval_bytes_and_remote_durable_record(self):
        result = handoff.deliver_webhook(self.export, self.users["notary"])
        self.assertEqual(result.status, "delivered")
        with sqlite3.connect(self.database) as db:
            rows = db.execute("SELECT checksum,payload,receipt FROM handoffs").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], hashlib.sha256(rows[0][1]).hexdigest())
        self.assertEqual(json.loads(rows[0][1])["data"]["Beteiligter"]["Kaeufer"]["Name"], "Anna Schmidt")
        self.assertEqual(json.loads(rows[0][2])["approval_hash"], self.matter.approval_hash)
        self.assertNotIn(b"REF-", bytes(result.receipt_enc))
        self.assertEqual(handoff.deliver_webhook(result, self.users["notary"]).delivery_attempts, 1)
        self.assertTrue(verify_chain(self.office).ok)

    def test_lost_response_retries_without_duplicate_remote_import(self):
        send = protocol.send
        def accepted_but_response_lost(*args):
            send(*args)
            raise ValidationError("Timed out after the destination committed.")
        with mock.patch.object(protocol, "send", side_effect=accepted_but_response_lost):
            first = handoff.deliver_webhook(self.export, self.users["notary"])
        self.assertEqual(first.status, "pending")
        self.matter.refresh_from_db()
        self.assertEqual(self.matter.status, "approved")
        second = handoff.deliver_webhook(first, self.users["notary"])
        self.assertEqual(second.status, "delivered")
        self.assertEqual(second.delivery_attempts, 2)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0], 1)

    def test_wrong_key_cannot_import_or_confirm_delivery(self):
        self.connector.signing_secret = "wrong-key-" + "x" * 40
        self.connector.save()
        result = handoff.deliver_webhook(self.export, self.users["notary"])
        self.assertEqual(result.status, "pending")
        self.assertFalse(result.receipt_enc)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0], 0)

    def test_http_cannot_be_acknowledged_using_a_typed_reference(self):
        with self.assertRaises(ValidationError):
            handoff.acknowledge(self.export, self.users["notary"], checksum=self.export.checksum, receipt_reference="MADE-UP")

    def test_role_gate_and_superseded_approval_block_network_send(self):
        with self.assertRaises(PermissionDenied):
            handoff.deliver_webhook(self.export, self.users["clerk"])
        services.reopen(self.matter, Membership.objects.get(tenant=self.office, user=self.users["notary"]))
        with self.assertRaises(ValidationError):
            handoff.deliver_webhook(self.export, self.users["notary"])
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0], 0)

    def test_loopback_exception_is_explicit_and_credentials_in_urls_rejected(self):
        with override_settings(LEXFLOW_WEBHOOK_ALLOW_LOOPBACK=False):
            with self.assertRaises(ValidationError):
                protocol.endpoint(self.connector)
        with override_settings(LEXFLOW_WEBHOOK_ENDPOINTS={"reference": "https://user:password@example.invalid/import"}):
            with self.assertRaises(ValidationError):
                protocol.endpoint(self.connector)

    def test_receipt_tampering_is_rejected(self):
        original = protocol.receipt_signature
        with mock.patch.object(protocol, "receipt_signature", side_effect=lambda secret, value: "0" * 64):
            result = handoff.deliver_webhook(self.export, self.users["notary"])
        self.assertEqual(result.status, "pending")
        self.assertIn("authentication", result.error)
        self.assertFalse(result.receipt_enc)
        self.assertNotEqual(original(self.secret, {}), "0" * 64)

    def test_timestamp_replay_outside_window_is_rejected(self):
        with mock.patch.object(protocol, "time", SimpleNamespace(time=lambda: 0)):
            result = handoff.deliver_webhook(self.export, self.users["notary"])
        self.assertEqual(result.status, "pending")
