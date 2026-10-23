import csv
from dataclasses import replace
from datetime import datetime, time, timedelta
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.connectors.models import Connector, Transfer
from apps.core.analytics import AnalyticsFilterForm, AnalyticsScope, build_report, elapsed, safe_csv_cell, timing
from apps.core.testing import make_office, property_matter, reset_vault
from apps.intake.models import Attachment, IntakeMessage
from apps.matters import services
from apps.matters.models import FieldCandidate, Matter
from apps.studio.models import MatterType


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"], LEXFLOW_DEMO_MODE=False)
class AnalyticsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.office, cls.users = make_office("analytics")
        cls.foreign, cls.foreign_users = make_office("outside")

    def setUp(self):
        reset_vault()
        self.now = timezone.now()
        self.today = timezone.localtime(self.now).date()
        self.scope = AnalyticsScope(self.today - timedelta(days=6), self.today, False, period="7")
        self.matter = property_matter(self.office, blatt_contract="812")
        self.received(self.matter, self.now - timedelta(days=1))

    def received(self, matter, at):
        IntakeMessage.objects.filter(pk=matter.message_id).update(received_at=at, processed_at=at + timedelta(seconds=9))
        Matter.objects.filter(pk=matter.pk).update(created_at=at)

    def report(self, scope=None, **kwargs):
        return build_report(self.office, scope or self.scope, now=self.now, **kwargs)

    def test_all_metrics_exclude_other_offices_other_cohorts_and_outside_dates(self):
        foreign = property_matter(self.foreign, blatt_contract="812")
        self.received(foreign, self.now - timedelta(days=1))
        demo = property_matter(self.office, blatt_contract="812")
        Matter.objects.filter(pk=demo.pk).update(is_demo=True)
        IntakeMessage.objects.filter(pk=demo.message_id).update(is_demo=True)
        self.received(demo, self.now - timedelta(days=1))
        old = property_matter(self.office, blatt_contract="812")
        self.received(old, self.now - timedelta(days=15))
        report = self.report()
        self.assertEqual(report["intake_count"], 1)
        self.assertEqual(report["matter_count"], 1)
        self.assertEqual(report["documents"]["files"], 3)
        self.assertEqual(report["quality"]["current_fields"], 9)
        self.assertEqual(sum(sum(s["data"]) for s in report["charts"]["daily"]["series"]), 1)
        self.assertEqual([row["ref"] for row in report["work_items"]], [self.matter.ref])
        synthetic = self.report(replace(self.scope, demo=True))
        self.assertEqual([row["ref"] for row in synthetic["work_items"]], [demo.ref])

    def test_date_window_uses_local_midnights_and_includes_the_entire_end_date(self):
        scope = replace(self.scope, end=self.today - timedelta(days=1))
        zone = timezone.get_current_timezone()
        first = timezone.make_aware(datetime.combine(scope.start, time.min), zone)
        last = timezone.make_aware(datetime.combine(scope.end + timedelta(days=1), time.min), zone)
        for index, at in enumerate((first - timedelta(microseconds=1), first, last - timedelta(microseconds=1), last)):
            IntakeMessage.objects.create(tenant=self.office, subject="Private date fixture", received_at=at,
                                         external_id=f"boundary-{index}")
        self.received(self.matter, first - timedelta(days=2))
        self.assertEqual(self.report(scope)["intake_count"], 2)

    def test_source_workflow_and_unprocessed_filters_are_applied_consistently(self):
        IntakeMessage.objects.create(tenant=self.office, subject="Private API fixture", source="api",
                                     received_at=self.now - timedelta(hours=1))
        report = self.report(replace(self.scope, source="api", workflow="@unprocessed"))
        self.assertEqual(report["intake_count"], 1)
        self.assertEqual(report["matter_count"], 0)
        self.assertEqual(report["documents"]["files"], 0)
        self.assertIsNone(report["quality"]["coverage"])
        self.assertEqual(sum(row["count"] for row in report["sources"]), 1)
        self.assertEqual(self.report(replace(self.scope, workflow="gmbh-formation"))["intake_count"], 0)
        self.assertEqual(self.report(replace(self.scope, workflow="property-purchase"))["intake_count"], 1)

    def test_future_intakes_are_not_counted_as_received(self):
        IntakeMessage.objects.create(tenant=self.office, subject="Future fixture", received_at=self.now + timedelta(minutes=1))
        self.assertEqual(self.report()["intake_count"], 1)

    @override_settings(LEXFLOW_DEMO_MODE=True)
    def test_demo_default_is_selected_in_the_rendered_cohort_control(self):
        form = AnalyticsFilterForm({}, tenant=self.office, now=self.now)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.scope().demo)
        self.assertIn('<option value="demo" selected>', str(form["cohort"]))
        self.assertIn('<option value="live">', str(form["cohort"]))

    def test_filter_validation_rejects_foreign_workflows_bad_ranges_and_demo_in_live_mode(self):
        MatterType.objects.create(tenant=self.foreign, key="foreign-only", name="Private foreign workflow")
        for values in (
            {"workflow": "foreign-only"}, {"cohort": "demo"}, {"source": "invalid"},
            {"period": "custom", "start_date": "invalid", "end_date": str(self.today)},
            {"period": "custom", "start_date": "0001-01-01", "end_date": "0001-01-07"},
            {"period": "custom", "start_date": str(self.today), "end_date": str(self.today - timedelta(days=1))},
            {"period": "custom", "start_date": str(self.today - timedelta(days=366)), "end_date": str(self.today)},
            {"period": "custom", "start_date": str(self.today), "end_date": str(self.today + timedelta(days=1))},
        ):
            with self.subTest(values=values):
                form = AnalyticsFilterForm(values, tenant=self.office, now=self.now)
                self.assertFalse(form.is_valid())
        valid = AnalyticsFilterForm({"period": "7"}, tenant=self.office, now=self.now)
        self.assertTrue(valid.is_valid(), valid.errors)
        self.assertEqual(valid.scope(), self.scope)
        self.assertNotIn("Private foreign workflow", str(valid))

    def test_previous_cohort_has_equal_length_without_overlapping_selected_dates(self):
        previous = property_matter(self.office, blatt_contract="812")
        self.received(previous, self.now - timedelta(days=8))
        report = self.report()
        self.assertEqual(report["previous_end"], self.scope.start - timedelta(days=1))
        self.assertEqual((report["previous_end"] - report["previous_start"]).days + 1, 7)
        self.assertIn("+0 (+0.0%)", report["cards"][0]["hint"])
        self.assertEqual(report["intake_count"], 1)

    def test_confidence_counts_fields_once_and_excludes_human_reviewed_values(self):
        before = sum(self.report()["charts"]["conf"]["series"][0]["data"])
        field = self.matter.fields.get(definition__key="buyer_name")
        candidate = field.candidates.first()
        for _ in range(3):
            FieldCandidate.objects.create(field=field, value=candidate.value, normalized=candidate.normalized,
                confidence=candidate.confidence, source=candidate.source, span_start=candidate.span_start,
                span_end=candidate.span_end, method=candidate.method, anchor=candidate.anchor)
        self.assertEqual(sum(self.report()["charts"]["conf"]["series"][0]["data"]), before)
        services.correct_field(field, self.users["clerk"], "Erika Example")
        self.assertEqual(sum(self.report()["charts"]["conf"]["series"][0]["data"]), before - 1)

    def test_first_extraction_and_distinct_corrections_survive_reextraction(self):
        coverage = self.report()["quality"]["coverage"]
        field = self.matter.fields.get(definition__key="buyer_name")
        services.correct_field(field, self.users["clerk"], "Erika Example")
        services.correct_field(field, self.users["clerk"], "Another Example")
        services.run_extraction(self.matter)
        services.correct_field(self.matter.fields.get(definition__key="buyer_name"), self.users["clerk"], "Final Example")
        report = self.report()
        self.assertEqual(report["quality"]["coverage"], coverage)
        self.assertEqual(report["quality"]["changed"], 1)
        self.assertEqual(report["reviewers"][0]["corrected"], 3)
        self.assertLessEqual(report["quality"]["correction_rate"], 100)

    def test_deliveries_pending_superseded_exports_and_http_retries_have_distinct_denominators(self):
        Matter.objects.filter(pk=self.matter.pk).update(approval_hash="a" * 64, status="approved")
        connector = Connector.objects.get(tenant=self.office, kind="json")
        specs = [("pending", "a" * 64, 3), ("exported", "b" * 64, 1), ("delivered", "a" * 64, 1), ("failed", "a" * 64, 0)]
        for status, approval_hash, attempts in specs:
            Transfer.objects.create(tenant=self.office, matter=self.matter, connector=connector, status=status,
                filename="demo.json", checksum="0" * 64, approval_hash=approval_hash, delivery_attempts=attempts,
                created_at=self.now - timedelta(hours=2), acknowledged_at=self.now - timedelta(hours=1) if status == "delivered" else None)
        report = self.report()
        self.assertEqual(report["transfer"]["total"], 4)
        self.assertEqual(report["transfer"]["receipt_rate"], 25)
        self.assertEqual(report["transfer"]["closed_rate"], 50)
        self.assertEqual(report["transfer"]["actionable"], 1)
        self.assertEqual(report["transfer"]["superseded"], 1)
        self.assertEqual(report["transfer"]["retries"], 2)
        self.assertEqual(report["timings"][2]["median"], 60)
        self.assertEqual(report["cards"][4]["value"], 1)

    def test_timing_uses_median_percentile_and_valid_completed_pairs(self):
        self.assertEqual(timing([5, 10, 25, None])["median"], 10)
        self.assertEqual(timing([5, 10, 25])["p90"], 22)
        self.assertIsNone(elapsed(self.now - timedelta(minutes=1), self.now, self.now))
        self.assertIsNone(elapsed(self.now + timedelta(minutes=1), self.now, self.now))
        Matter.objects.filter(pk=self.matter.pk).update(approved_at=self.now - timedelta(days=2))
        self.assertEqual(self.report()["timings"][1]["n"], 0)

    def test_scans_and_original_acknowledgement_counts_exclude_email_bodies(self):
        for method, reviewed in (("pdf_ocr", None), ("ocr_partial", None), ("ocr", self.now)):
            Attachment.objects.create(tenant=self.office, message=self.matter.message, filename="fixture.pdf",
                                      text_method=method, reviewed_at=reviewed, page_count=2)
        Attachment.objects.create(tenant=self.office, message=self.matter.message, filename="Email", kind="body", text_method="needs_ocr")
        report = self.report()
        self.assertEqual(report["quality"]["scan_documents"], 3)
        self.assertEqual(report["quality"]["scan_reviews"], 2)
        self.assertEqual(report["documents"]["files"], 6)

    def test_empty_data_and_unrecorded_baselines_do_not_invent_zero_rates(self):
        report = self.report(replace(self.scope, source="folder"))
        self.assertEqual(report["intake_count"], 0)
        self.assertIsNone(report["quality"]["coverage"])
        self.assertIsNone(report["transfer"]["closed_rate"])
        self.assertIsNone(report["timings"][1]["median"])
        self.assertFalse(report["baseline"]["available"])
        self.assertIsNone(report["baseline"]["minutes"])
        self.assertTrue(all(chart["empty"] for chart in report["charts"].values()))

    def test_report_aggregation_does_not_decrypt_confidential_case_values(self):
        with mock.patch("apps.core.crypto.TenantCipher.decrypt_text", side_effect=AssertionError("Unexpected private field decryption")):
            report = self.report(chain=SimpleNamespace(ok=True, checked=4))
        self.assertEqual(report["matter_count"], 1)

    def test_broken_audit_chain_cannot_report_successful_coverage(self):
        Matter.objects.filter(pk=self.matter.pk).update(approved_at=self.now, approval_hash="a" * 64)
        report = self.report(chain=SimpleNamespace(ok=False, checked=3))
        self.assertEqual(report["audit"]["coverage"], 0)
        self.assertEqual(report["audit"]["complete"], 0)

    def test_csv_uses_same_scope_excludes_private_values_and_neutralizes_formulas(self):
        field = self.matter.fields.get(definition__key="buyer_name").definition
        field.label = "=1+1"
        field.save(update_fields=["label"])
        workflow = self.matter.matter_type
        workflow.name = "=1+1"
        workflow.save(update_fields=["name"])
        self.client.force_login(self.users["clerk"])
        response = self.client.get(reverse("core:analytics_export"), self.scope.parameters())
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        text = response.content.decode("utf-8-sig")
        rows = list(csv.reader(StringIO(text)))
        self.assertEqual(rows[0], ["Section", "Metric", "Group", "Value", "Unit", "Denominator"])
        self.assertIn("'=1+1", text)
        self.assertNotIn("Anna Schmidt", text)
        self.assertNotIn("Sonnenallee", text)
        self.assertNotIn("850000", text)
        self.assertIn(["Summary", "Intakes received", "", "1", "intakes", ""], rows)
        self.assertEqual(safe_csv_cell("   =2+2"), "'   =2+2")
        self.assertEqual(safe_csv_cell("\tunsafe"), "'\tunsafe")
        self.assertEqual(self.client.get(reverse("core:analytics_export"), {"workflow": "not-owned"}).status_code, 400)

    def test_report_and_export_require_authentication_and_valid_office_membership(self):
        self.assertEqual(self.client.get(reverse("core:analytics_export")).status_code, 302)
        self.client.force_login(self.foreign_users["clerk"])
        response = self.client.get(reverse("core:analytics_export"), self.scope.parameters())
        self.assertEqual(response.status_code, 200)
        self.assertIn('Intakes received,,0', response.content.decode("utf-8-sig"))
        self.client.force_login(self.users["clerk"])
        response = self.client.get(reverse("core:dashboard"), {"cohort": "demo"})
        self.assertIsNone(response.context["analytics"])
        self.assertContains(response, "Correct the filters above")

    def test_case_pagination_retains_scope_and_field_links(self):
        for index in range(26):
            IntakeMessage.objects.create(tenant=self.office, subject="Private pagination fixture",
                                         received_at=self.now - timedelta(hours=1), source="api")
        self.client.force_login(self.users["clerk"])
        response = self.client.get(reverse("core:dashboard"), {**self.scope.parameters(), "case_page": "2"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["analytics"]["intake_count"], 27)
        self.assertEqual(len(response.context["case_page"]), 2)
        self.assertContains(response, "case_page=1#analytics-workload")

    def test_report_queries_do_not_grow_per_case(self):
        for _ in range(5):
            matter = property_matter(self.office, blatt_contract="812")
            self.received(matter, self.now - timedelta(days=1))
        with CaptureQueriesContext(connection) as queries:
            report = self.report(chain=SimpleNamespace(ok=True, checked=24))
        self.assertEqual(report["matter_count"], 6)
        self.assertLess(len(queries), 25, "Analytics should aggregate in batches rather than query each case")
