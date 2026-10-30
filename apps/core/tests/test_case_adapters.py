"""End-to-end contracts for structured cases and destination extension points."""
import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse

from apps.audit.services import verify_chain
from apps.connectors.mapping import apply_mapping, validate_rules
from apps.connectors.models import Connector, MappingProfile, Transfer
from apps.connectors.profiles import configure_profile
from apps.connectors.services import approval_snapshot, render, transfer
from apps.core.models import Membership
from apps.core.testing import make_office, property_matter, reset_vault
from apps.matters import services
from apps.matters.case_data import canonical_case, revision, save_details, validate_details
from apps.matters.models import Matter


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CaseAdapterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.office, cls.users = make_office()
        cls.other, cls.other_users = make_office("other")

    def setUp(self):
        reset_vault()
        self.matter = property_matter(self.office, blatt_contract="812")
        self.membership = Membership.objects.get(tenant=self.office, user=self.users["notary"])
        self.xml = Connector.objects.get(tenant=self.office, kind="xml")
        self.json = Connector.objects.get(tenant=self.office, kind="json")

    def configure(self, **kwargs):
        return configure_profile(self.office, self.json, {
            "key": "property-case", "name": "Property case", "matter_type": "property-purchase", "rules": [], **kwargs})

    def approve(self):
        services.confirm_field(self.matter.fields.get(definition__key="purchase_price"), self.users["clerk"])
        services.approve(self.matter, self.membership)

    def save(self, group, values, **kwargs):
        return save_details(self.matter, self.users["clerk"], group, values,
                            expected_revision=revision(self.matter), **kwargs)

    def test_existing_cases_project_primary_records_without_migrating_private_data(self):
        self.assertEqual(self.matter.case_data, {})
        case = canonical_case(self.matter)
        self.assertEqual(case["participants"][0]["name"], "Anna Schmidt")
        self.assertEqual(case["participants"][0]["birth_date"], "1985-03-12")
        self.assertEqual(case["properties"][0]["parcel"], "1234/5")
        validate_details(self.matter)

    def test_additional_parties_properties_are_encrypted_and_exported_from_approval(self):
        self.configure()
        self.save("participants", {"kind": "legal", "role": "buyer", "name": "Synthetic Buyer GmbH",
                                   "register_number": "HRB 123", "ownership_share": "1/2"})
        self.save("properties", {"address": "Example Street 2", "district": "Test district", "parcel": "234/5"})
        self.save("metadata", {"designation": "Seller/Buyer KV", "case_code": "KV", "master_file_reference": "MASTER-1"})
        with connection.cursor() as cursor:
            cursor.execute("SELECT case_data FROM matters_matter WHERE id = %s", [self.matter.pk])
            raw = cursor.fetchone()[0]
        self.assertTrue(raw.startswith("lfx:v1:"))
        self.assertNotIn("Synthetic Buyer", raw)
        self.approve()
        self.json.refresh_from_db()
        exported = transfer(self.matter, self.json, self.users["notary"])
        case = json.loads(exported.payload)["data"]
        self.assertEqual(len(case["participants"]), 3)
        self.assertEqual(len(case["properties"]), 2)
        self.assertEqual(case["metadata"]["case_code"], "KV")
        self.assertEqual(exported.status, "exported")
        self.assertEqual(transfer(self.matter, self.json, self.users["notary"]).pk, exported.pk)
        self.assertTrue(verify_chain(self.office).ok)
        # Even a direct model write cannot change the approved representation.
        self.matter.case_data = {"metadata": {"designation": "Changed after approval"}}
        self.matter.save(update_fields=["case_data"])
        self.assertEqual(json.loads(render(self.json, self.matter)[0])["data"], case)

    def test_primary_enrichment_cannot_override_extracted_values_and_tracks_edits(self):
        self.save("participants", {"nationality": "German", "tax_id": "synthetic-id"}, record_id="buyer")
        case = canonical_case(self.matter)
        self.assertEqual(case["participants"][0]["nationality"], "German")
        self.assertEqual(case["participants"][0]["name"], "Anna Schmidt")
        old_revision = revision(self.matter)
        services.correct_field(self.matter.fields.get(definition__key="buyer_name"), self.users["clerk"], "Erika Example")
        self.assertEqual(canonical_case(self.matter)["participants"][0]["name"], "Erika Example")
        with self.assertRaisesMessage(ValidationError, "changed while"):
            save_details(self.matter, self.users["clerk"], "metadata", {"case_code": "KV"}, expected_revision=old_revision)
        with self.assertRaisesMessage(ValidationError, "Source-bound"):
            self.save("participants", {"name": "Unreviewed replacement"}, record_id="buyer")

    def test_locked_cases_and_foreign_staff_cannot_change_details(self):
        with self.assertRaises(PermissionDenied):
            save_details(self.matter, self.other_users["clerk"], "metadata", {}, expected_revision=revision(self.matter))
        stale = Matter.objects.get(pk=self.matter.pk)
        self.approve()
        with self.assertRaisesMessage(ValidationError, "locked"):
            save_details(stale, self.users["clerk"], "metadata", {}, expected_revision=revision(stale))

    def test_legacy_export_refuses_to_drop_enriched_data(self):
        self.save("metadata", {"external_case_reference": "TEST-123"})
        self.approve()
        with self.assertRaisesMessage(ValidationError, "additional details"):
            transfer(self.matter, self.xml, self.users["notary"])
        self.assertEqual(Transfer.objects.count(), 0)

    def test_mapping_revision_is_frozen_and_new_destinations_need_reapproval(self):
        first = self.configure(rules=[{"source": "fields.buyer_name", "target": "Purchaser.Name", "required": True}])
        self.approve()
        self.json.refresh_from_db()
        before = render(self.json, self.matter)[0]
        second = self.configure(rules=[{"source": "fields.seller_name", "target": "Vendor.Name"}])
        self.assertEqual(second.version, first.version + 1)
        self.json.refresh_from_db()
        self.assertEqual(render(self.json, self.matter)[0], before)
        first.name = "Changed"
        with self.assertRaisesMessage(ValidationError, "immutable"):
            first.save()
        other_connector = Connector.objects.create(tenant=self.office, name="Another mapping", kind="json", mapping_profile=second)
        with self.assertRaisesMessage(ValidationError, "not included"):
            transfer(self.matter, other_connector, self.users["notary"])

    def test_blocked_vendor_adapter_cannot_preview_or_export(self):
        self.configure(adapter="pronotar32", target_version="Unknown")
        self.json.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "not implemented"):
            render(self.json, self.matter)
        self.approve()
        with self.assertRaisesMessage(ValidationError, "not implemented"):
            transfer(self.matter, self.json, self.users["notary"])
        self.assertFalse(Transfer.objects.exists())

    def test_profile_tenant_workflow_and_format_boundaries(self):
        profile = self.configure()
        foreign = Connector.objects.get(tenant=self.other, kind="json")
        foreign.mapping_profile = profile
        with self.assertRaises(ValidationError):
            foreign.full_clean()
        with self.assertRaises(ValidationError):
            render(foreign, self.matter)
        with self.assertRaises(ValidationError):
            configure_profile(self.office, self.xml, {"key": "invalid", "name": "Invalid", "matter_type": "property-purchase"})
        self.assertFalse(MappingProfile.objects.filter(key="invalid").exists())
        self.configure(matter_type="gmbh-formation")
        self.json.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "matching workflow"):
            render(self.json, self.matter)

    def test_required_mapping_values_fail_without_creating_a_transfer(self):
        self.configure(rules=[{"source": "metadata.external_case_reference", "target": "Case.Reference", "required": True}])
        self.approve()
        self.json.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "Mapping requires"):
            transfer(self.matter, self.json, self.users["notary"])
        self.assertFalse(Transfer.objects.exists())

    def test_representative_relationships_reject_cycles_and_dangling_ids(self):
        self.save("participants", {"kind": "natural", "role": "representative", "name": "Example Representative", "represents": "buyer"})
        representative = self.matter.case_data["participants"][0]["id"]
        with self.assertRaisesMessage(ValidationError, "cycle"):
            self.save("participants", {"represents": representative}, record_id="buyer")
        self.matter.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.save("participants", {"kind": "natural", "role": "seller", "name": "X", "represents": "foreign-id"})

    def test_invalid_case_shapes_and_values_block_approval(self):
        bad_values = [{"metadata": []}, {"participants": ["not-an-object"]}, {"unknown": "x"},
                      {"participants": [{"id": "buyer", "name": "bypass"}]},
                      {"participants": [{"id": "buyer", "tax_id": ["not-text"]}]}]
        for invalid in bad_values:
            self.matter.case_data = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                validate_details(self.matter)

    def test_case_details_page_validates_preserves_input_and_enforces_ownership(self):
        self.client.force_login(self.users["clerk"])
        url = reverse("matters:case_details", args=[self.matter.ref])
        self.assertContains(self.client.get(url), "Case references")
        response = self.client.post(url, {"group": "participants", "revision": revision(self.matter),
            "participants-new-kind": "legal", "participants-new-role": "seller", "participants-new-name": "Example Company"})
        self.assertEqual(response.status_code, 302)
        self.matter.refresh_from_db()
        self.assertEqual(self.matter.case_data["participants"][0]["name"], "Example Company")
        response = self.client.post(url, {"group": "participants", "revision": revision(self.matter),
            "participants-new-kind": "natural", "participants-new-role": "buyer", "participants-new-name": "Preserve Input",
            "participants-new-birth_date": "not a date"})
        self.assertContains(response, "Preserve Input", status_code=400)
        self.client.force_login(self.other_users["clerk"])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {"group": "metadata"}).status_code, 404)

    def test_primary_form_only_saves_enrichment_and_delete_clears_it(self):
        self.client.force_login(self.users["clerk"])
        url = reverse("matters:case_details", args=[self.matter.ref])
        response = self.client.post(url, {"group": "participants", "record_id": "buyer", "revision": revision(self.matter),
            "participants-buyer-kind": "natural", "participants-buyer-name": "Forged name", "participants-buyer-tax_id": "Synthetic tax ID"})
        self.assertEqual(response.status_code, 302)
        self.matter.refresh_from_db()
        self.assertNotIn("name", self.matter.case_data["participants"][0])
        self.assertEqual(canonical_case(self.matter)["participants"][0]["name"], "Anna Schmidt")
        response = self.client.post(url, {"group": "participants", "record_id": "buyer", "revision": revision(self.matter), "action": "delete"})
        self.assertEqual(response.status_code, 302)
        self.matter.refresh_from_db()
        self.assertEqual(self.matter.case_data["participants"], [])

    def test_enable_structured_exports_is_admin_only_and_idempotent(self):
        url = reverse("connectors:enable_structured")
        self.client.force_login(self.users["clerk"])
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.users["admin"])
        self.assertEqual(self.client.post(url).status_code, 302)
        count = Connector.objects.filter(tenant=self.office).count()
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(Connector.objects.filter(tenant=self.office).count(), count)
        response = self.client.get(reverse("connectors:index"))
        self.assertContains(response, "ProNotar32")
        self.assertContains(response, "Not implemented")
        self.assertEqual(MappingProfile.objects.filter(tenant=self.other).count(), 0)

    def test_approved_api_serves_only_frozen_data_with_office_scope(self):
        token = self.office.issue_api_token()
        url = reverse("api_approved_case", args=[self.matter.ref])
        self.assertEqual(self.client.get(url).status_code, 401)
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        self.assertEqual(self.client.get(url, **headers).status_code, 409)
        self.approve()
        response = self.client.get(url, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.json()["case"], approval_snapshot(self.matter)["case"])
        other_token = self.other.issue_api_token()
        self.assertEqual(self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {other_token}").status_code, 404)
        services.reopen(self.matter, self.membership)
        self.assertEqual(self.client.get(url, **headers).status_code, 409)

    def test_profile_command_allocates_revisions_and_rejects_unknown_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps({"key": "operator", "name": "Operator", "matter_type": "property-purchase", "rules": []}))
            out = StringIO()
            call_command("configure_mapping_profile", tenant=self.office.slug, connector=self.json.pk, spec=path, stdout=out)
            self.assertIn("v1", out.getvalue())
            self.assertEqual(MappingProfile.objects.get(key="operator").adapter_key, "canonical-json-v1")

    def test_mapping_typos_and_bulk_revision_changes_are_rejected(self):
        with self.assertRaisesMessage(ValidationError, "Unknown case source"):
            self.configure(rules=[{"source": "fields.buyer_nmae", "target": "Name"}])
        self.assertFalse(MappingProfile.objects.exists())
        profile = self.configure()
        with self.assertRaisesMessage(ValidationError, "immutable"):
            MappingProfile.objects.filter(pk=profile.pk).update(rules=[])

    def test_missing_property_identifier_and_invalid_share_are_rejected(self):
        for group, values in (("properties", {}), ("participants", {
                "kind": "natural", "role": "buyer", "name": "Example", "ownership_share": "1/0"})):
            with self.subTest(group=group), self.assertRaises(ValidationError):
                self.save(group, values)
            self.matter.refresh_from_db()
        self.assertEqual(self.matter.case_data, {})

    def test_changing_workflow_requires_removing_saved_records(self):
        self.save("participants", {"tax_id": "test"}, record_id="buyer")
        from apps.studio.models import MatterType
        target = MatterType.objects.get(tenant=self.office, key="gmbh-formation")
        with self.assertRaisesMessage(ValidationError, "Remove saved"):
            services.set_matter_type(self.matter, self.users["clerk"], target)


class MappingContractTests(SimpleTestCase):
    def test_repeated_items_preserve_cardinality_and_required_values(self):
        rules = [{"source": "participants", "target": "People", "required": True, "items": [
            {"source": "name", "target": "Name", "required": True}, {"source": "external_id", "target": "Identity.External"}]}]
        data = {"participants": [{"name": "A"}, {"name": "B", "external_id": "B-2"}]}
        self.assertEqual(apply_mapping(data, rules), {"People": [{"Name": "A"}, {"Name": "B", "Identity": {"External": "B-2"}}]})
        with self.assertRaisesMessage(ValidationError, "participants[1].name"):
            apply_mapping({"participants": [{"name": "A"}, {}]}, rules)

    def test_prefix_collisions_unsafe_paths_and_bad_rule_shapes_are_rejected(self):
        examples = [
            [{"source": "ref", "target": p} for p in ("A", "A0", "A.B")],
            [{"source": "fields.buyer_name", "target": "x[0]"}],
            [{"source": "__class__", "target": "X"}],
            [{"source": "ref", "target": "X", "required": "false"}],
            [{"source": "participants", "target": "X", "items": [{"source": "properties", "target": "Y", "items": []}]}],
            [{"source": "ref", "target": "X", "expression": "exec()"}],
        ]
        for rules in examples:
            with self.subTest(rules=rules), self.assertRaises(ValidationError):
                validate_rules(rules)

    def test_zero_values_are_not_treated_as_missing(self):
        self.assertEqual(apply_mapping({"fields": {"value": 0}}, [{"source": "fields.value", "target": "Value", "required": True}]), {"Value": 0})
