"""Explicit code registry: transports never decide a vendor's document format.

Add a versioned adapter here after obtaining the vendor contract and fixtures.
Never resolve arbitrary Python modules from operator or intake data.
"""
import json
from dataclasses import dataclass

from django.core.exceptions import ValidationError

from .mapping import apply_mapping


@dataclass(frozen=True)
class CanonicalJSONAdapter:
    key: str = "canonical-json-v1"
    label: str = "LexFlow case JSON v1"
    available: bool = True
    description: str = "Parties, properties and reviewed fields for adapter development. Not a ProNotar import."

    def render(self, snapshot, profile):
        case = snapshot.get("case")
        if not case or case.get("schema") != "lexflow.case/1":
            raise ValidationError("Reopen and approve this case to include the structured case schema.")
        data = apply_mapping(case, profile["rules"])
        body = {key: snapshot[key] for key in ("ref", "matter_type", "approved_at", "approved_by")}
        body.update(schema="lexflow.handoff/1", case_schema="lexflow.case/1",
                    data_class=snapshot.get("data_class", "office"),
                    mapping={key: profile[key] for key in ("key", "version", "digest", "adapter")}, data=data)
        return (json.dumps(body, indent=2, ensure_ascii=False) + "\n",
                f"{snapshot['ref']}-case-v1.json", "application/json", _count(data))


@dataclass(frozen=True)
class ProNotar32Adapter:
    key: str = "pronotar32"
    label: str = "ProNotar32"
    available: bool = False
    description: str = "Awaiting the office's ProNotar version, supported import schema and accepted sample files."

    def render(self, snapshot, profile):
        raise ValidationError("ProNotar32 delivery is not implemented. Obtain the vendor import specification and validate an adapter first.")


def _count(value):
    if isinstance(value, dict):
        return sum(_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count(item) for item in value)
    return int(value is not None and value != "")


ADAPTERS = {adapter.key: adapter for adapter in (CanonicalJSONAdapter(), ProNotar32Adapter())}


def get_adapter(key):
    try:
        return ADAPTERS[key]
    except KeyError:
        raise ValidationError("Unknown destination adapter; install a supported version before exporting.")
