import json

from django.test import TestCase

from apps.audit.models import AuditEvent, AuditImmutable
from apps.audit.services import verify_chain
from apps.core.testing import make_office, property_matter, reset_vault
from apps.matters import services


class AuditChainTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        reset_vault()
        cls.tenant, cls.users = make_office()
        cls.other, _ = make_office("beta", "Notariat Beta")

    def setUp(self):
        self.matter = property_matter(self.tenant)

    def test_chain_verifies_and_is_per_tenant(self):
        property_matter(self.other)
        report = verify_chain(self.tenant)
        self.assertTrue(report.ok)
        self.assertGreater(report.checked, 3)
        self.assertEqual(AuditEvent.objects.filter(tenant=self.tenant).order_by("seq").first().seq, 1)
        self.assertEqual(AuditEvent.objects.filter(tenant=self.other).order_by("seq").first().seq, 1)
        self.assertTrue(verify_chain(self.other).ok)

    def test_tampering_is_detected(self):
        target = AuditEvent.objects.filter(tenant=self.tenant).order_by("seq")[1]
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("UPDATE audit_auditevent SET summary = %s WHERE id = %s", ["Nothing happened", target.pk])
        report = verify_chain(self.tenant)
        self.assertFalse(report.ok)
        self.assertEqual(report.broken_at, target.seq)

    def test_deleted_event_is_detected(self):
        target = AuditEvent.objects.filter(tenant=self.tenant).order_by("seq")[1]
        AuditEvent.objects.filter(pk=target.pk)._raw_delete(AuditEvent.objects.db)
        self.assertFalse(verify_chain(self.tenant).ok)

    def test_events_are_immutable(self):
        e = AuditEvent.objects.filter(tenant=self.tenant).first()
        e.summary = "edited"
        with self.assertRaises(AuditImmutable):
            e.save()
        with self.assertRaises(AuditImmutable):
            e.delete()

    def test_corrections_store_digests_not_values(self):
        field = self.matter.fields.get(definition__key="buyer_name")
        services.correct_field(field, self.users["clerk"], "Erika Mustermann")
        ev = AuditEvent.objects.filter(tenant=self.tenant, action="field.corrected").get()
        blob = json.dumps(ev.details, ensure_ascii=False)
        self.assertNotIn("Mustermann", blob)
        self.assertNotIn("Anna", blob)
        self.assertIn("to_digest", ev.details)
        self.assertEqual(ev.actor, self.users["clerk"])
