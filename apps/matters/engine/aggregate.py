"""Combine candidates from all documents into one decision per field.

  * agreement across documents raises confidence (independent evidence)
  * disagreement is a conflict: confidence drops and transfer is blocked
  * nothing found for a required field is reported as missing
"""
from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal

CAP = Decimal("0.99")


@dataclass
class FieldDecision:
    definition: object
    status: str
    value: str = ""
    normalized: str = ""
    confidence: Decimal = Decimal("0")
    best: object = None
    candidates: list = field(default_factory=list)
    note: str = ""


def decide(definition, candidates) -> FieldDecision:
    # A duplicated upload or repeated label is not independent evidence.
    unique = {}
    for candidate in candidates:
        source = getattr(candidate.attachment, "sha256", "") or getattr(candidate.attachment, "pk", None) or id(candidate.attachment)
        key = (source, candidate.normalized.casefold())
        if key not in unique or candidate.confidence > unique[key].confidence:
            unique[key] = candidate
    candidates = list(unique.values())
    if not candidates:
        if definition.required:
            return FieldDecision(definition, "missing", note="Not found in any document.")
        return FieldDecision(definition, "auto", note="Optional and not provided.")
    groups = OrderedDict()
    for c in sorted(candidates, key=lambda c: c.confidence, reverse=True):
        groups.setdefault(c.normalized.casefold(), []).append(c)

    if len(groups) == 1:
        group = next(iter(groups.values()))
        miss = Decimal("1")
        for c in group:
            miss *= Decimal("1") - c.confidence
        confidence = min(CAP, Decimal("1") - miss).quantize(Decimal("0.0001"))
        best = group[0]
        if definition.always_review:
            status, note = "review", "Office policy: always confirmed by a person."
        elif confidence >= definition.auto_threshold:
            status, note = "auto", ""
        else:
            status, note = "review", "Confidence below the prefill threshold."
        if len(group) > 1:
            note = (note + " " if note else "") + f"Agrees across {len(group)} documents."
        return FieldDecision(definition, status, best.value, best.normalized, confidence, best, candidates, note.strip())

    weights = {k: sum(c.confidence for c in g) for k, g in groups.items()}
    total = sum(weights.values())
    lead_key = max(weights, key=weights.get)
    lead = groups[lead_key]
    confidence = (weights[lead_key] / total * lead[0].confidence).quantize(Decimal("0.0001"))
    others = [g[0] for k, g in groups.items() if k != lead_key]
    note = "Documents disagree: " + "; ".join(
        f"{o.attachment.display_name} states {o.normalized or o.value}" for o in others
    )
    return FieldDecision(definition, "conflict", lead[0].value, lead[0].normalized, confidence, lead[0], candidates, note)
