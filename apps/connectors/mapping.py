"""Bounded declarative mappings; no expressions, code, constants or network access."""
import hashlib
import json
import re

from django.core.exceptions import ValidationError

PATH = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)*$")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def validate_rules(rules, *, item=False):
    if not isinstance(rules, list) or len(rules) > 150:
        raise ValidationError("Mappings must be a list of at most 150 rules.")
    paths = []
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {"source", "target", "required", "items"}:
            raise ValidationError("A mapping rule supports source, target, required and items only.")
        for key in ("source", "target"):
            path = rule.get(key)
            if not isinstance(path, str) or len(path) > 120 or not PATH.fullmatch(path) or len(path.split(".")) > 8:
                raise ValidationError("Mapping paths must be simple dotted field names, at most eight levels deep.")
        if not isinstance(rule.get("required", False), bool):
            raise ValidationError("A mapping's required flag must be true or false.")
        if not item and rule["source"].split(".")[0] not in {"metadata", "fields", "participants", "properties", "ref", "matter_type"}:
            raise ValidationError("A source must reference the canonical case schema.")
        if "items" in rule:
            if item or rule["source"] not in ("participants", "properties") or not rule["items"]:
                raise ValidationError("Only participants and properties support one level of item mappings.")
            validate_rules(rule["items"], item=True)
        paths.append(rule["target"])
    # Check every prefix, not only adjacent lexical names (A, A0, A.B).
    seen = set(paths)
    if len(seen) != len(paths) or any(".".join(p.split(".")[:i]) in seen for p in paths for i in range(1, len(p.split(".")))):
        raise ValidationError("Mapping targets overlap; give every output a unique leaf path.")


def validate_sources(rules, field_keys):
    from apps.matters.case_data import METADATA_FIELDS, PARTICIPANT_FIELDS, PROPERTY_FIELDS
    validate_rules(rules)
    sources = {"ref", "matter_type", "metadata", "fields", "participants", "properties"}
    sources.update(f"fields.{key}" for key in field_keys)
    sources.update(f"metadata.{key}" for key in METADATA_FIELDS)
    for rule in rules:
        if rule["source"] not in sources:
            raise ValidationError(f"Unknown case source: {rule['source']}.")
        if "items" in rule:
            fields = PARTICIPANT_FIELDS if rule["source"] == "participants" else PROPERTY_FIELDS
            if any(item["source"] not in {*fields, "id"} for item in rule["items"]):
                raise ValidationError("An item mapping references an unknown record field.")


def _lookup(source, path):
    node = source
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def apply_mapping(case, rules):
    validate_rules(rules)
    if not rules:
        return case

    def project(source, rules, context="case"):
        result = {}
        for rule in rules:
            value = _lookup(source, rule["source"])
            if rule.get("required") and (value is None or value == "" or value == [] or value == {}):
                raise ValidationError(f"Mapping requires {context}.{rule['source']}.")
            if value is None or value == "":
                continue
            if "items" in rule:
                if not isinstance(value, list):
                    raise ValidationError("Collection mapping requires a list of records.")
                value = [project(row, rule["items"], f"{rule['source']}[{index}]") for index, row in enumerate(value)]
            node = result
            parts = rule["target"].split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        return result
    return project(case, rules)
