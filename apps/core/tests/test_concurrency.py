"""These tests exercise real row locks and deliberately skip on SQLite."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import threading
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase, override_settings

from apps.audit.services import record, verify_chain
from apps.connectors.models import Connector, Transfer
from apps.connectors.services import transfer
from apps.core.models import Membership, Tenant
from apps.core.testing import make_office, property_matter, reset_vault
from apps.intake.models import IntakeMessage
from apps.matters import services
from apps.matters.models import ExtractedField


@skipUnless(connection.vendor == "postgresql", "Requires actual PostgreSQL row locking")
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PostgreSQLConcurrencyTests(TransactionTestCase):
    def setUp(self):
        reset_vault()
        self.office, self.users = make_office()
        self.matter = property_matter(self.office, blatt_contract="812")
        services.confirm_field(self.matter.fields.get(definition__key="purchase_price"), self.users["clerk"])
        self.notary = Membership.objects.get(tenant=self.office, user=self.users["notary"])

    def parallel(self, count, action):
        barrier = threading.Barrier(count)
        def worker(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return action(index)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(worker, range(count)))

    def test_edit_waits_for_approval_then_rejects_stale_mutation(self):
        ready = threading.Event()
        def edit():
            close_old_connections()
            try:
                field = ExtractedField.objects.select_related("matter").get(matter=self.matter, definition__key="buyer_name")
                ready.set()
                try:
                    services.correct_field(field, self.users["clerk"], "Erika Mustermann")
                    return "changed"
                except ValidationError:
                    return "blocked"
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Tenant.objects.select_for_update().get(pk=self.office.pk)
                future = pool.submit(edit)
                self.assertTrue(ready.wait(5))
                with self.assertRaises(TimeoutError):
                    future.result(timeout=.25)
                services.approve(self.matter, self.notary)
            self.assertEqual(future.result(timeout=10), "blocked")
        self.assertEqual(self.matter.fields.get(definition__key="buyer_name").normalized, "Anna Schmidt")
        self.assertTrue(verify_chain(self.office).ok)

    def test_parallel_exports_create_one_idempotent_transfer(self):
        services.approve(self.matter, self.notary)
        connector = Connector.objects.get(tenant=self.office, kind="xml")
        ids = self.parallel(4, lambda _: transfer(self.matter, connector, self.users["notary"]).pk)
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(Transfer.objects.count(), 1)
        self.assertTrue(verify_chain(self.office).ok)

    def test_parallel_intake_deduplicates_same_external_id(self):
        ids = self.parallel(4, lambda _: services.ingest_message(self.office, subject="Synthetic intake", body="Example",
                           external_id="same-scanner-id", process=False).pk)
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(IntakeMessage.objects.filter(external_id="same-scanner-id").count(), 1)
        self.assertTrue(verify_chain(self.office).ok)

    def test_parallel_audit_writes_form_one_unbroken_chain(self):
        self.office.refresh_from_db()
        previous = self.office.audit_seq
        self.parallel(6, lambda i: record(self.office, "test.parallel", "Concurrent event", details={"index": i}).pk)
        self.office.refresh_from_db()
        self.assertEqual(self.office.audit_seq, previous + 6)
        self.assertTrue(verify_chain(self.office).ok)

    def test_audit_verification_is_consistent_during_parallel_appends(self):
        def action(index):
            if index % 2:
                return verify_chain(self.office).ok
            record(self.office, "test.concurrent_verify", "Append while others verify")
            return True
        self.assertTrue(all(self.parallel(8, action)))
        self.assertTrue(verify_chain(self.office).ok)
