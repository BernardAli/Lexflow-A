import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.connectors.models import Connector, Transfer
from apps.core.testing import PASSWORD, make_office, property_files, property_matter, reset_vault
from apps.intake.models import Attachment, IntakeMessage
from apps.matters.models import Matter
from apps.studio.models import FieldDefinition


class ViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        reset_vault()
        cls.tenant, cls.users = make_office()
        cls.other, cls.other_users = make_office("beta", "Notariat Beta")

    def setUp(self):
        self.matter = property_matter(self.tenant)
        self.foreign = property_matter(self.other)

    def login(self, role="clerk", users=None):
        self.client.login(username=(users or self.users)[role].username, password=PASSWORD)

    def test_login_required(self):
        self.assertEqual(self.client.get(reverse("core:login")).status_code, 200)
        r = self.client.get(reverse("core:dashboard"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("core:login"), r["Location"])

    def test_every_page_renders_for_every_role(self):
        att = self.matter.message.files.first()
        xml = Connector.objects.get(tenant=self.tenant, kind="xml")
        urls = [
            reverse("core:dashboard"), reverse("intake:inbox"), f"{reverse('intake:inbox')}?m={self.matter.message.ref}",
            reverse("intake:new"), reverse("matters:list"), f"{reverse('matters:list')}?status=needs_review&q=Kauf",
            reverse("matters:review", args=[self.matter.ref]), reverse("studio:index"),
            reverse("studio:matter_type", args=["property-purchase"]), reverse("studio:matter_type", args=["gmbh-formation"]),
            reverse("connectors:index"), reverse("audit:index"), f"{reverse('audit:index')}?group=matter",
            reverse("audit:export"), reverse("core:security"), reverse("intake:taskpane", args=[self.matter.message.ref]),
            reverse("intake:download", args=[att.pk]), reverse("connectors:preview", args=[self.matter.ref, xml.pk]),
        ]
        for role in ("clerk", "notary", "admin"):
            self.login(role)
            for url in urls:
                with self.subTest(role=role, url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)

    def test_review_page_shows_conflict_and_marks(self):
        self.login()
        r = self.client.get(reverse("matters:review", args=[self.matter.ref]))
        self.assertContains(r, "Transfer blocked")
        self.assertContains(r, "Resolve the conflict on Land register sheet")
        self.assertContains(r, 'class="lf-mark lf-mark--seal" data-field="grundbuch"')

    def test_tenant_isolation(self):
        self.login()
        foreign_att = self.foreign.message.files.first()
        for url in (reverse("matters:review", args=[self.foreign.ref]),
                    reverse("intake:download", args=[foreign_att.pk]),
                    f"{reverse('intake:inbox')}?m={self.foreign.message.ref}",
                    reverse("intake:taskpane", args=[self.foreign.message.ref])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        r = self.client.get(reverse("matters:list"))
        self.assertNotContains(r, self.foreign.ref)
        self.assertContains(r, self.matter.ref)
        f = self.foreign.fields.first()
        r = self.client.post(reverse("matters:field_action", args=[self.foreign.ref, f.pk]), {"action": "confirm"})
        self.assertEqual(r.status_code, 404)

    def test_full_review_approve_transfer_flow_via_http(self):
        self.login("clerk")
        gb = self.matter.fields.get(definition__key="grundbuch")
        cand = gb.candidates.get(source__doc_type__key="grundbuchauszug")
        self.client.post(reverse("matters:field_action", args=[self.matter.ref, gb.pk]), {"action": "choose", "candidate": cand.pk})
        price = self.matter.fields.get(definition__key="purchase_price")
        self.client.post(reverse("matters:field_action", args=[self.matter.ref, price.pk]), {"action": "confirm"})
        self.matter.refresh_from_db()
        self.assertEqual(self.matter.status, Matter.Status.READY)
        self.client.post(reverse("matters:approve", args=[self.matter.ref]))
        self.matter.refresh_from_db()
        self.assertEqual(self.matter.status, Matter.Status.READY, "a clerk must not approve")
        xml = Connector.objects.get(tenant=self.tenant, kind="xml")
        self.assertEqual(self.client.post(reverse("connectors:send", args=[self.matter.ref]), {"connector": xml.pk}).status_code, 403)

        self.client.logout()
        self.login("notary")
        self.client.post(reverse("matters:approve", args=[self.matter.ref]))
        self.client.post(reverse("connectors:send", args=[self.matter.ref]), {"connector": xml.pk})
        self.matter.refresh_from_db()
        self.assertEqual(self.matter.status, Matter.Status.APPROVED)
        t = Transfer.objects.get(matter=self.matter)
        self.assertEqual(t.status, "exported")
        self.client.post(reverse("connectors:acknowledge", args=[t.ref]),
                         {"checksum": t.checksum, "receipt_reference": "TEST-DESTINATION-RECEIPT"})
        self.matter.refresh_from_db()
        self.assertEqual(self.matter.status, Matter.Status.TRANSFERRED)
        r = self.client.get(reverse("connectors:download", args=[t.ref]))
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"<Bezeichnung>M\xc3\xbcnchen Blatt 812</Bezeichnung>", r.content)
        self.assertEqual(r["X-LexFlow-SHA256"], t.checksum)
        self.client.logout()
        self.login("notary", self.other_users)
        self.assertEqual(self.client.get(reverse("connectors:download", args=[t.ref])).status_code, 404)

    def test_new_intake_upload(self):
        self.login()
        files = [SimpleUploadedFile(n, d, content_type=c) for n, c, d in property_files(blatt_contract="812")]
        r = self.client.post(reverse("intake:new"), {"subject": "Kaufvertrag Upload", "body": "", "source": "upload",
                                                    "process": "on", "files": files})
        msg = IntakeMessage.objects.filter(tenant=self.tenant).order_by("-pk").first()
        self.assertRedirects(r, reverse("matters:review", args=[msg.matter.ref]))
        self.assertEqual(msg.files.count(), 3)
        self.assertEqual(msg.matter.open_conflicts.count(), 0)

    def test_new_intake_rejects_bad_files(self):
        self.login()
        r = self.client.post(reverse("intake:new"), {"subject": "x", "files": [SimpleUploadedFile("a.exe", b"MZ")]})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Only PDF, text and image files")

    def test_process_button(self):
        self.login()
        msg = IntakeMessage.objects.create(tenant=self.tenant, subject="Kauf später")
        Attachment.objects.create(tenant=self.tenant, message=msg, kind="body", filename="Email text")
        r = self.client.post(reverse("intake:process", args=[msg.ref]))
        msg.refresh_from_db()
        self.assertEqual(msg.status, "processed")
        self.assertRedirects(r, reverse("matters:review", args=[msg.matter.ref]))

    def test_studio_requires_admin(self):
        fdef = FieldDefinition.objects.get(matter_type__tenant=self.tenant, key="purchase_price")
        url = reverse("studio:save_field", args=["property-purchase", fdef.pk])
        data = {"label": "Kaufpreis", "destination_path": "Vorgang.Preis", "auto_threshold": "0.97", "required": "on"}
        self.login("clerk")
        self.assertEqual(self.client.post(url, data).status_code, 403)
        self.client.logout()
        self.login("admin")
        self.client.post(url, data)
        fdef.refresh_from_db()
        self.assertEqual(fdef.destination_path, "Vorgang.Preis")
        self.assertFalse(fdef.always_review)
        self.assertTrue(AuditEvent.objects.filter(tenant=self.tenant, action="studio.field_changed").exists())
        bad = dict(data, destination_path="not a path")
        self.client.post(url, bad)
        fdef.refresh_from_db()
        self.assertEqual(fdef.destination_path, "Vorgang.Preis")

    def test_security_settings_admin_only(self):
        self.login("clerk")
        self.assertEqual(self.client.post(reverse("core:security"), {"retention_days": "90"}).status_code, 403)
        self.client.logout()
        self.login("admin")
        self.client.post(reverse("core:security"), {
            "security_profile": "private", "retention_days": "90", "baseline_minutes_per_matter": "40",
            "baseline_rekey_error_pct": "5", "baseline_audit_completeness_pct": "50"})
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.security_profile, "private")
        self.assertEqual(self.tenant.retention_days, 90)
        r = self.client.post(reverse("core:regenerate_token"), follow=True)
        self.assertContains(r, "lfx_")


class ApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        reset_vault()
        cls.tenant, _ = make_office()
        cls.other, _ = make_office("beta", "Notariat Beta")
        cls.token = cls.tenant.issue_api_token()
        cls.other_token = cls.other.issue_api_token()

    def test_requires_token(self):
        self.assertEqual(self.client.post(reverse("api_intake"), {"subject": "x"}).status_code, 401)
        r = self.client.post(reverse("api_intake"), {"subject": "x"}, HTTP_AUTHORIZATION="Bearer lfx_wrong")
        self.assertEqual(r.status_code, 401)

    def test_intake_and_read_back(self):
        files = [SimpleUploadedFile(n, d, content_type=c) for n, c, d in property_files()]
        r = self.client.post(reverse("api_intake"), {"subject": "Kauf via Outlook", "files": files},
                             HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual(body["matter"]["matter_type"], "property-purchase")
        self.assertTrue(body["matter"]["blocked"])
        ref = body["matter"]["ref"]
        ok = self.client.get(reverse("api_matter", args=[ref]), HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(ok.status_code, 200)
        cross = self.client.get(reverse("api_matter", args=[ref]), HTTP_AUTHORIZATION=f"Bearer {self.other_token}")
        self.assertEqual(cross.status_code, 404)


class CommandTests(TestCase):
    def test_folder_agent_and_purge(self):
        reset_vault()
        tenant, users = make_office()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = root / "Kauf_Leopoldstrasse"
            case.mkdir()
            for name, _, data in property_files(blatt_contract="812"):
                (case / name).write_bytes(data)
            (root / "notiz.txt").write_text("Rückruf erbeten")
            call_command("lexflow_folder_agent", tenant=tenant.slug, path=str(root), stdout=StringIO())
            self.assertEqual(IntakeMessage.objects.filter(tenant=tenant, source="folder").count(), 2)
            self.assertTrue((root / ".processed" / "Kauf_Leopoldstrasse").exists())
        m = next(m for m in Matter.objects.filter(tenant=tenant) if m.title == "Kauf Leopoldstrasse")
        m.status = Matter.Status.TRANSFERRED
        m.transferred_at = timezone.now() - timedelta(days=400)
        m.save()
        call_command("lexflow_purge", stdout=StringIO())
        att = m.message.files.first()
        self.assertEqual(att.text_method, "purged")
        self.assertFalse((tenant.vault_dir / (att.sha256[:32] + ".lfx")).exists())
        self.assertTrue(AuditEvent.objects.filter(tenant=tenant, action="retention.purged").exists())

    def test_seed_is_idempotent_with_additional_connectors(self):
        reset_vault()
        call_command("seed_lexflow", stdout=StringIO())
        tenant = Connector.objects.get(name="Notary software import", tenant__slug="keller").tenant
        reference = Connector.objects.create(
            tenant=tenant, name="HTTP reference receiver", kind=Connector.Kind.JSON,
            delivery=Connector.Delivery.WEBHOOK, endpoint_key="reference", signing_secret="r" * 48)
        extra_xml = Connector.objects.create(tenant=tenant, name="Additional XML import", kind=Connector.Kind.XML)
        counts = (Matter.objects.count(), IntakeMessage.objects.count(), FieldDefinition.objects.count(),
                  Connector.objects.count(), Transfer.objects.count(), AuditEvent.objects.count())
        call_command("seed_lexflow", stdout=StringIO())
        self.assertEqual(counts, (Matter.objects.count(), IntakeMessage.objects.count(),
                                  FieldDefinition.objects.count(), Connector.objects.count(),
                                  Transfer.objects.count(), AuditEvent.objects.count()))
        reference.refresh_from_db()
        self.assertEqual(reference.delivery, Connector.Delivery.WEBHOOK)
        self.assertEqual(reference.signing_secret, "r" * 48)
        self.assertFalse(Transfer.objects.filter(connector__in=[reference, extra_xml]).exists())
        self.assertTrue(Matter.objects.filter(status="transferred").exists())
        self.assertTrue(Matter.objects.filter(status="unclassified").exists())
