"""Pilot measurement (deck slide 14): baseline before automation vs. measured with LexFlow."""
from collections import OrderedDict
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, Q
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.audit.services import verify_chain
from apps.connectors.models import Transfer
from apps.matters.models import ExtractedField, Matter

D1 = Decimal("0.1")


def _pct(part, whole):
    if not whole:
        return None
    return (Decimal(part) * 100 / Decimal(whole)).quantize(D1, rounding=ROUND_HALF_UP)


def pipeline_counts(tenant):
    m = Matter.objects.filter(tenant=tenant)
    return [
        ("Receive", "Email, PDF, scan", m.count()),
        ("Classify", "Matter and document type", m.exclude(matter_type=None).count()),
        ("Extract", "Entities and fields", ExtractedField.objects.filter(matter__tenant=tenant).exclude(value="").count()),
        ("Validate", "Rules, confidence, conflicts",
         ExtractedField.objects.filter(matter__tenant=tenant, initial_status__in=["review", "conflict", "missing"]).count()),
        ("Review", "People check exceptions",
         m.filter(status__in=[Matter.Status.NEEDS_REVIEW, Matter.Status.UNCLASSIFIED, Matter.Status.READY]).count()),
        ("Transfer", "Approved connector", Transfer.objects.filter(tenant=tenant, status="delivered").count()),
        ("Audit", "Who did what, when", AuditEvent.objects.filter(tenant=tenant).count()),
    ]


def pilot_metrics(tenant, *, demo=False):
    matters = Matter.objects.filter(tenant=tenant, is_demo=demo)
    total = found = auto = low = corrected = 0
    for matter in matters:
        initial = matter.initial_metrics
        if not initial:
            fields = list(matter.fields.select_related("definition"))
            initial = {"total": len(fields), "found": sum(bool(f.value) for f in fields),
                       "auto": sum(f.initial_status == "auto" and bool(f.value) for f in fields),
                       "low": sum(f.initial_status in ("conflict", "missing") or
                                  (f.initial_status == "review" and not f.definition.always_review) for f in fields)}
        total += initial["total"]
        found += initial["found"]
        auto += initial["auto"]
        low += initial["low"]
        corrected += len(matter.corrected_keys)
    approved = matters.filter(approved_at__isnull=False)
    minutes = [(m.approved_at - m.created_at).total_seconds() / 60 for m in approved]
    avg_minutes = Decimal(str(sum(minutes) / len(minutes))).quantize(D1) if minutes else None
    transfers = Transfer.objects.filter(tenant=tenant, matter__is_demo=demo)
    delivered = transfers.filter(status="delivered").count()
    closed = transfers.filter(status__in=["delivered", "failed"]).count()
    chain = verify_chain(tenant)
    complete = 0
    for matter in approved:
        actions = set(AuditEvent.objects.filter(tenant=tenant, object_ref=matter.ref).values_list("action", flat=True))
        need = {"matter.classified", "matter.extracted", "matter.approved"}
        if matter.status == Matter.Status.TRANSFERRED:
            need.add("matter.transferred")
        complete += bool(matter.approval_hash) and need <= actions
    audit_pct = _pct(complete, approved.count()) if chain.ok else Decimal("0")
    has_baseline = bool(tenant.baseline_recorded_at) or demo
    return [
        {"label": "Elapsed time to approval", "hint": "Includes queue time; not staff effort", "unit": "min",
         "baseline": None, "pilot": avg_minutes, "better": "lower"},
        {"label": "Fields extracted", "hint": "Initial extraction coverage", "unit": "%",
         "baseline": Decimal("0") if has_baseline else None, "pilot": _pct(found, total), "better": "higher"},
        {"label": "Fields prefilled", "hint": "Initial values without review", "unit": "%",
         "baseline": None, "pilot": _pct(auto, total), "better": "higher"},
        {"label": "Human correction rate", "hint": "Distinct fields whose values changed", "unit": "%",
         "baseline": tenant.baseline_rekey_error_pct if has_baseline else None,
         "pilot": _pct(corrected, total), "better": "lower"},
        {"label": "Low-confidence rate", "hint": "Initial exceptions; excludes policy-only checks", "unit": "%",
         "baseline": None, "pilot": _pct(low, total), "better": "lower"},
        {"label": "Confirmed delivery", "hint": "Receipts / closed attempts; pending excluded", "unit": "%",
         "baseline": None, "pilot": _pct(delivered, closed), "better": "higher"},
        {"label": "Audit completeness", "hint": "Frozen approvals with required events", "unit": "%",
         "baseline": tenant.baseline_audit_completeness_pct if has_baseline else None,
         "pilot": audit_pct, "better": "higher"},
    ]

def daily_series(tenant, days=14):
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    buckets = OrderedDict(((start + timedelta(days=i)), {"clean": 0, "reviewed": 0, "conflict": 0}) for i in range(days))
    qs = Matter.objects.filter(tenant=tenant, created_at__date__gte=start).annotate(
        conflicts=Count("fields", filter=Q(fields__initial_status="conflict")),
        opened=Count("fields", filter=Q(fields__initial_status="missing")
                     | (Q(fields__initial_status="review") & Q(fields__definition__always_review=False))),
    )
    for m in qs:
        day = timezone.localtime(m.created_at).date()
        if day not in buckets:
            continue
        key = "conflict" if m.conflicts else ("reviewed" if m.opened else "clean")
        buckets[day][key] += 1
    return {
        "labels": [d.strftime("%d %b") for d in buckets],
        "clean": [b["clean"] for b in buckets.values()],
        "reviewed": [b["reviewed"] for b in buckets.values()],
        "conflict": [b["conflict"] for b in buckets.values()],
    }


def confidence_histogram(tenant):
    edges = [(0, 50), (50, 70), (70, 80), (80, 90), (90, 95), (95, 101)]
    values = ExtractedField.objects.filter(matter__tenant=tenant).exclude(value="").values_list("candidates__confidence", flat=True)
    counts = [0] * len(edges)
    for v in values:
        if v is None:
            continue
        p = float(v) * 100
        for i, (lo, hi) in enumerate(edges):
            if lo <= p < hi:
                counts[i] += 1
                break
    return {"labels": ["<50", "50–69", "70–79", "80–89", "90–94", "95+"], "counts": counts}
