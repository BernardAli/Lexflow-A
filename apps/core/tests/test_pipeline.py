import hashlib
import hmac
import json
from decimal import Decimal
from unittest import mock

from cryptography.fernet import InvalidToken
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings

from apps.audit.models import AuditEvent
from apps.connectors.models import Connector
from apps.connectors.services import acknowledge, transfer
from apps.core.models import Membership
from apps.core.testing import make_office, property_matter, reset_vault
from apps.intake.models import IntakeMessage
from apps.matters import services
from apps.matters.engine.providers import AnthropicProvider, RuleProvider, get_provider
from apps.matters.models import Matter
from apps.studio.models import FieldDefinition, MatterType


class PipelineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        reset_vault()
        cls.tenant, cls.users = make_office()

    def setUp(self):
        self.matter = property_matter(self.tenant)
        self.f = {f.definition.key: f for f in self.matter.fields.select_related("definition")}

    def test_classification_and_conflict(self):
        self.assertEqual(self.matter.matter_type.key, "property-purchase")
        self.assertEqual(self.matter.status, Matter.Status.NEEDS_REVIEW)
        gb = self.f["grundbuch"]
        self.assertEqual(gb.status, "conflict")
        self.assertEqual(gb.normalized, "München Blatt 812")
        self.assertLess(gb.confidence, Decimal("0.70"))
        self.assertEqual(len({candidate.normalized for candidate in gb.candidates.all()}), 2)
        self.assertEqual(self.f["buyer_name"].status, "auto")
        self.assertGreaterEqual(self.f["buyer_name"].confidence, Decimal("0.99"))
        self.assertEqual(self.f["purchase_price"].normalized, "850000.00")
        self.assertEqual(self.f["purchase_price"].status, "review")  # policy: always confirm

    def test_documents_are_classified(self):
        kinds = set(self.matter.message.files.values_list("doc_type__key", flat=True))
        self.assertEqual(kinds, {"kaeuferfragebogen", "grundbuchauszug", "kaufvertragsentwurf"})

    def test_approval_rules(self):
        notary = Membership.objects.get(user=self.users["notary"])
        clerk = Membership.objects.get(user=self.users["clerk"])
        with self.assertRaises(ValidationError):
            services.approve(self.matter, notary)
        cand = self.f["grundbuch"].candidates.get(source__doc_type__key="grundbuchauszug")
        services.choose_candidate(self.f["grundbuch"], self.users["clerk"], cand)
        services.confirm_field(self.f["purchase_price"], self.users["clerk"])
        self.matter.refresh_from_db()
        self.assertEqual(self.matter.status, Matter.Status.READY)
        with self.assertRaises(PermissionDenied):
            services.approve(self.matter, clerk)
        services.approve(self.matter, notary)
        self.matter.refresh_from_db()
        self.assertEqual(self.matter.status, Matter.Status.APPROVED)
        with self.assertRaises(ValidationError):
            services.correct_field(self.f["buyer_name"], self.users["clerk"], "Someone Else")

    def _approved(self):
        services.correct_field(self.f["grundbuch"], self.users["clerk"], "München Blatt 812")
        services.confirm_field(self.f["purchase_price"], self.users["clerk"])
        services.approve(self.matter, Membership.objects.get(user=self.users["notary"]))
        self.matter.refresh_from_db()
        return self.matter

    def test_transfer_requires_approval(self):
        with self.assertRaises(ValidationError):
            transfer(self.matter, Connector.objects.get(tenant=self.tenant, kind="xml"), self.users["notary"])

    def test_xml_transfer(self):
        m = self._approved()
        t = transfer(m, Connector.objects.get(tenant=self.tenant, kind="xml"), self.users["notary"])
        body = t.payload
        self.assertIn("<Kaeufer>", body)
        self.assertIn("<Name>Anna Schmidt</Name>", body)
        self.assertIn("<Kaufpreis>850000.00</Kaufpreis>", body)
        self.assertIn("<Bezeichnung>München Blatt 812</Bezeichnung>", body)
        self.assertEqual(t.checksum, hashlib.sha256(body.encode()).hexdigest())
        self.assertNotIn(b"Anna Schmidt", bytes(t.payload_enc))
        m.refresh_from_db()
        self.assertEqual(m.status, Matter.Status.APPROVED)
        self.assertEqual(t.status, "exported")
        acknowledge(t, self.users["notary"], checksum=t.checksum, receipt_reference="TEST-RECEIPT")
        m.refresh_from_db()
        self.assertEqual(m.status, Matter.Status.TRANSFERRED)

    def test_json_transfer_is_signed(self):
        m = self._approved()
        t = transfer(m, Connector.objects.get(tenant=self.tenant, kind="json"), self.users["notary"])
        expected = hmac.new(b"s3cret", t.payload.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(t.signature, expected)
        self.assertEqual(json.loads(t.payload)["data"]["Beteiligter"]["Verkaeufer"]["Name"], "Peter Müller")

    def test_correction_validates_type(self):
        with self.assertRaises(ValidationError):
            services.correct_field(self.f["purchase_price"], self.users["clerk"], "a lot")
        services.correct_field(self.f["purchase_price"], self.users["clerk"], "€900,000")
        self.f["purchase_price"].refresh_from_db()
        self.assertEqual(self.f["purchase_price"].normalized, "900000.00")
        self.assertEqual(self.f["purchase_price"].revisions.count(), 1)

    def test_teach_learns_label_not_value(self):
        att = self.matter.message.files.get(doc_type__key="kaeuferfragebogen")
        text = att.text
        start = text.index("089 1234567")
        field = self.f["buyer_name"]
        learned = services.teach_from_selection(self.matter, self.users["clerk"], att, start, start + 11, field)
        self.assertIsNone(learned)
        fdef = FieldDefinition.objects.get(pk=field.definition_id)
        self.assertNotIn("Telefon", fdef.anchors)
        self.assertNotIn("089 1234567", json.dumps(fdef.anchors))
        self.assertFalse(AuditEvent.objects.filter(tenant=self.tenant, action="studio.anchor_learned").exists())

    def test_unclassified_then_set_type(self):
        msg = services.ingest_message(self.tenant, subject="Scan 7", files=[("s.txt", "text/plain", b"Bitte um Rueckruf.")])
        m = msg.matter
        self.assertEqual(m.status, Matter.Status.UNCLASSIFIED)
        self.assertEqual(m.fields.count(), 0)
        services.set_matter_type(m, self.users["clerk"], MatterType.objects.get(tenant=self.tenant, key="gmbh-formation"))
        m.refresh_from_db()
        self.assertEqual(m.fields.count(), 6)
        self.assertEqual(m.status, Matter.Status.NEEDS_REVIEW)

    def test_unprocessed_message_then_process(self):
        from apps.core.testing import property_files
        msg = services.ingest_message(self.tenant, subject="Kauf", files=property_files(blatt_contract="812"), process=False)
        self.assertEqual(msg.status, IntakeMessage.Status.NEW)
        self.assertFalse(Matter.objects.filter(message=msg).exists())
        m = services.process_message(msg)
        self.assertEqual(m.open_conflicts.count(), 0)


class EncryptionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        reset_vault()
        cls.a, _ = make_office("alpha")
        cls.b, _ = make_office("beta", "Notariat Beta")

    def test_documents_encrypted_at_rest_with_tenant_key(self):
        m = property_matter(self.a)
        att = m.message.files.first()
        raw = (self.a.vault_dir / att.blob_name).read_bytes()
        self.assertTrue(raw.startswith(b"gAAAA"))
        self.assertNotIn(b"%PDF", raw)
        self.assertTrue(att.read_bytes().startswith(b"%PDF"))
        self.assertNotIn(b"Schmidt", bytes(att.text_enc))
        self.assertIn("Schmidt", att.text)
        self.assertNotEqual(self.a.key_fingerprint, self.b.key_fingerprint)
        with self.assertRaises(InvalidToken):
            self.b.cipher.decrypt(raw)
        self.assertNotEqual(self.a.vault_dir, self.b.vault_dir)


class ProviderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        reset_vault()
        cls.tenant, _ = make_office()

    @override_settings(LEXFLOW_AI_PROVIDER="anthropic")
    def test_external_ai_requires_tenant_permission(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            self.assertIsInstance(get_provider(self.tenant), RuleProvider)
            m = property_matter(self.tenant)
            self.assertTrue(AuditEvent.objects.filter(tenant=self.tenant, action="policy.external_ai_blocked",
                                                      object_ref=m.ref).exists())
            self.tenant.allow_external_ai = True
            self.assertIsInstance(get_provider(self.tenant), AnthropicProvider)

    def test_anthropic_provider_maps_quotes_to_spans(self):
        defs = list(MatterType.objects.get(tenant=self.tenant, key="property-purchase").fields.all())
        text = "Käufer: Anna Schmidt\nKaufpreis: 850.000,00 EUR\n"
        att = mock.Mock(display_name="doc.pdf")
        reply = {"content": [{"type": "tool_use", "input": {
            "buyer_name": {"value": "Anna Schmidt", "source_quote": "Anna Schmidt", "confidence": 0.95},
            "purchase_price": {"value": "850000", "source_quote": "not in the text 850.000,00 EUR", "confidence": 0.9},
        }}]}
        p = AnthropicProvider(api_key="sk-test", model="claude-sonnet-5-5")
        with mock.patch.object(p, "_call_api", return_value=reply) as call:
            cands = {c.field_key: c for c in p.extract(att, text, defs)}
        self.assertEqual(call.call_args[0][0]["tool_choice"]["name"], "record_fields")
        self.assertEqual(text[cands["buyer_name"].span_start:cands["buyer_name"].span_end], "Anna Schmidt")
        self.assertNotIn("purchase_price", cands)  # ungrounded values are discarded
