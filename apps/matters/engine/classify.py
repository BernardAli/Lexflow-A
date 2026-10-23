"""Document and matter classification by weighted phrase evidence.

Deterministic and explainable: every decision lists the phrases that drove it,
so a reviewer can see why a document was treated as a land register extract.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class DocDecision:
    doc_type: object | None
    confidence: Decimal
    hits: list = field(default_factory=list)


@dataclass
class MatterDecision:
    matter_type: object | None
    confidence: Decimal
    scores: dict = field(default_factory=dict)


MIN_MATTER_CONFIDENCE = Decimal("0.50")


def _hits(text: str, phrases):
    low = text.casefold()
    return [p for p in phrases if p and p.casefold() in low]


def classify_document(text: str, doc_types) -> DocDecision:
    if not text.strip():
        return DocDecision(None, Decimal("0"))
    best, best_hits = None, []
    for dt in doc_types:
        hits = _hits(text[:6000], dt.keywords)
        if len(hits) > len(best_hits):
            best, best_hits = dt, hits
    if not best:
        return DocDecision(None, Decimal("0"))
    confidence = min(Decimal("0.99"), Decimal("0.62") + Decimal("0.12") * len(best_hits))
    return DocDecision(best, confidence, best_hits)


def classify_matter(subject: str, doc_decisions, matter_types) -> MatterDecision:
    scores = defaultdict(Decimal)
    for mt in matter_types:
        scores[mt] += Decimal("0.75") * len(_hits(subject, mt.subject_keywords))
    for d in doc_decisions:
        if d.doc_type is not None and d.doc_type.matter_type_id:
            mt = next((m for m in matter_types if m.pk == d.doc_type.matter_type_id), None)
            if mt is not None:
                scores[mt] += d.doc_type.weight * d.confidence
    total = sum(scores.values())
    if not total:
        return MatterDecision(None, Decimal("0"), {})
    best = max(scores, key=lambda m: scores[m])
    share = scores[best] / total
    saturation = min(Decimal("1"), Decimal("0.55") + Decimal("0.18") * scores[best])
    confidence = (share * saturation).quantize(Decimal("0.0001"))
    named = {m.name: float(s) for m, s in scores.items()}
    if confidence < MIN_MATTER_CONFIDENCE:
        return MatterDecision(None, confidence, named)
    return MatterDecision(best, min(confidence, Decimal("0.99")), named)
