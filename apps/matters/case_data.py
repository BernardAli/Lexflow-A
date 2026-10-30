"""Vendor-neutral case schema with source-bound primary records.

Enrichment and additional records are encrypted on Matter. Adapters consume
the canonical data frozen in an approval, never mutable field values.
"""
import hashlib
import json
import uuid
from copy import deepcopy

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.audit.services import record
from apps.core.models import Membership

SCHEMA = "lexflow.case/1"
MAX_RECORDS = 100
PARTICIPANT_FIELDS = {
    "kind": "Person / organisation", "role": "Role", "name": "Full name / company name",
    "birth_name": "Birth name", "birth_date": "Date of birth", "address": "Address",
    "postal_code": "Postal code", "city": "City", "country": "Country",
    "email": "Email", "phone": "Telephone", "tax_id": "Tax ID",
    "nationality": "Nationality", "marital_status": "Marital status",
    "marital_regime": "Marital property regime", "legal_form": "Legal form",
    "registered_seat": "Registered seat", "register_court": "Register court",
    "register_number": "Register number", "vat_id": "VAT ID",
    "ownership_share": "Ownership / shareholding fraction", "share_amount": "Share amount (EUR)",
    "represents": "Represents participant", "external_id": "Destination person / company ID",
}
PROPERTY_FIELDS = {
    "address": "Property address", "postal_code": "Postal code", "city": "City",
    "land_register": "Land register (Grundbuch)", "sheet": "Sheet (Blatt)",
    "volume": "Volume (Band)", "inventory_number": "Inventory number (BV)",
    "district": "Cadastral district (Gemarkung)", "section": "Section (Flur)",
    "parcel": "Parcel (Flurstück)", "area": "Area (m²)", "ownership_share": "Co-ownership fraction",
    "use": "Use / location description", "category": "Property category",
    "external_id": "Destination property ID",
}
METADATA_FIELDS = {
    "designation": "Case designation (Aktenbezeichnung)", "case_code": "Office case abbreviation",
    "master_file_reference": "Master file reference (Stammakte)",
    "external_case_reference": "Destination case reference",
}
PRIMARY = {
    "property-purchase": {
        "participants": {
            "buyer": ({"kind": "natural", "role": "buyer"}, {"name": "buyer_name", "birth_date": "buyer_birthdate", "address": "buyer_address"}),
            "seller": ({"kind": "natural", "role": "seller"}, {"name": "seller_name"}),
        },
        "properties": {"property": ({}, {"address": "property_address", "land_register": "grundbuch", "parcel": "parcel"})},
    },
    "gmbh-formation": {"participants": {
        "company": ({"kind": "legal", "role": "company"}, {"name": "company_name", "registered_seat": "seat"}),
        "director": ({"kind": "natural", "role": "director"}, {"name": "managing_director", "birth_date": "director_birthdate"}),
    }},
    "power-of-attorney": {"participants": {
        "principal": ({"kind": "natural", "role": "principal"}, {"name": "principal_name", "birth_date": "principal_birthdate", "address": "principal_address"}),
        "attorney": ({"kind": "natural", "role": "attorney"}, {"name": "attorney_name"}),
    }},
}


def bindings(matter, group):
    key = matter.matter_type.key if matter.matter_type else ""
    return PRIMARY.get(key, {}).get(group, {})


def canonical_case(matter, fields=None):
    fields = fields if fields is not None else {
        f.definition.key: f.normalized or f.value for f in matter.fields.select_related("definition")
    }
    stored = matter.case_data or {}
    if not isinstance(stored, dict) or not isinstance(stored.get("metadata", {}), dict) or any(
        not isinstance(stored.get(group, []), list) or any(not isinstance(item, dict) or
            not isinstance(item.get("id"), str) for item in stored.get(group, []))
        for group in ("participants", "properties")
    ):
        raise ValidationError("Case details have an unsupported structure.")
    result = {"schema": SCHEMA, "ref": matter.ref,
              "matter_type": matter.matter_type.key if matter.matter_type else "",
              "metadata": deepcopy(stored.get("metadata", {})), "fields": fields}
    for group in ("participants", "properties"):
        entries = {item["id"]: deepcopy(item) for item in stored.get(group, [])}
        primary = []
        for key, (defaults, links) in bindings(matter, group).items():
            item = {**defaults, **entries.pop(key, {}), "id": key}
            item.update({name: fields.get(source, "") for name, source in links.items()})
            primary.append(item)
        result[group] = primary + list(entries.values())
    return result


def revision(matter):
    return hashlib.sha256(json.dumps(canonical_case(matter), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_details(matter):
    from .case_forms import detail_form
    stored = matter.case_data
    if not isinstance(stored, dict) or set(stored) - {"metadata", "participants", "properties"}:
        raise ValidationError("Case details have an unsupported structure.")
    canonical_case(matter)
    for group in ("metadata", "participants", "properties"):
        entries = [stored.get(group, {})] if group == "metadata" else stored.get(group, [])
        if not isinstance(entries, list) or len(entries) > MAX_RECORDS:
            raise ValidationError("A case supports at most 100 records per collection.")
        seen = set()
        for item in entries:
            key = item.get("id", "")
            primary = bindings(matter, group).get(key)
            if group != "metadata":
                if key in seen:
                    raise ValidationError("Case record IDs must be unique.")
                seen.add(key)
                if not primary:
                    try:
                        uuid.UUID(key)
                    except (ValueError, TypeError, AttributeError):
                        raise ValidationError("Unknown case record ID.")
            fields = {"metadata": METADATA_FIELDS, "participants": PARTICIPANT_FIELDS, "properties": PROPERTY_FIELDS}[group]
            if set(item) - set(fields) - ({"id"} if group != "metadata" else set()):
                raise ValidationError("Case record contains unknown fields.")
            if primary and set(item) & set(primary[1]):
                raise ValidationError("Source-bound values must be edited in Extracted data.")
            if primary and "role" in item and item["role"] != primary[0].get("role"):
                raise ValidationError("A primary participant's role belongs to the workflow.")
            if any(not isinstance(value, str) for value in item.values()):
                raise ValidationError("Case values must be text.")
            form = detail_form(matter, group, key, data={**(primary[0] if primary else {}), **item})
            if not form.is_valid():
                raise ValidationError([f"{fields.get(name, group)}: {error}" for name, errors in form.errors.items() for error in errors])
    parties = canonical_case(matter)["participants"]
    ids = {item["id"] for item in parties}
    links = {item["id"]: item.get("represents", "") for item in parties}
    for key, target in links.items():
        visited = {key}
        while target:
            if target not in ids or target in visited:
                raise ValidationError("Representation must reference another participant without a cycle.")
            visited.add(target)
            target = links.get(target)


@transaction.atomic
def save_details(matter, user, group, values, *, record_id="", expected_revision, delete=False):
    from .services import _lock_matter
    _lock_matter(matter)
    if not Membership.objects.filter(tenant=matter.tenant, user=user).exists():
        raise PermissionDenied("Office membership is required.")
    if not matter.matter_type:
        raise ValidationError("Classify the matter before entering case details.")
    if expected_revision != revision(matter):
        raise ValidationError("The case changed while this form was open. Reload it before saving.")
    if group not in ("metadata", "participants", "properties"):
        raise ValidationError("Unknown case section.")
    before = revision(matter)
    data = deepcopy(matter.case_data or {})
    if group == "metadata":
        if delete:
            data.pop(group, None)
        else:
            data[group] = values
    else:
        entries = data.get(group, [])
        primary = bindings(matter, group).get(record_id)
        found = any(item["id"] == record_id for item in entries)
        if record_id and not primary and not found:
            raise ValidationError("That record does not belong to this case.")
        key = record_id or str(uuid.uuid4())
        entries = [item for item in entries if item["id"] != key]
        if not delete:
            entries.append({**values, "id": key})
        data[group] = entries
    matter.case_data = data
    validate_details(matter)
    matter.save(update_fields=["case_data"])
    record(matter.tenant, "matter.details_changed", "Reviewed case details updated", actor=user, obj=matter,
           details={"section": group, "record": record_id or (key if group != "metadata" else ""),
                    "before_digest": before, "after_digest": revision(matter), "deleted": delete})
    return matter


def has_details(matter):
    return any(matter.case_data.get(group) for group in ("participants", "properties")) or any(
        matter.case_data.get("metadata", {}).values())
