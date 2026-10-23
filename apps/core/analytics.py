"""Office-scoped intake cohort analytics, without reading confidential case values.

Dates select intakes by received_at. Their current case, field and delivery
outcomes are measured at report generation, including later review/receipts.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from statistics import mean, median
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.db.models import BooleanField, Case, Count, Value, When
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.audit.services import verify_chain
from apps.connectors.models import Transfer
from apps.intake.models import Attachment, IntakeMessage
from apps.matters.models import ExtractedField, Matter
from apps.studio.models import MatterType

RANGES = [("7", "Last 7 days"), ("14", "Last 14 days"), ("30", "Last 30 days"),
          ("90", "Last 90 days"), ("custom", "Custom dates")]
SCAN_METHODS = {"ocr", "pdf_ocr", "needs_ocr", "ocr_partial"}
METHOD_LABELS = {"pdf_text": "Digital PDF", "plain": "Text", "pdf_ocr": "Scanned PDF read",
                 "ocr": "Image read", "needs_ocr": "Manual reading needed", "ocr_partial": "Partial OCR",
                 "purged": "Purged under retention", "unsupported": "Unsupported", "": "Not processed"}


class AnalyticsFilterForm(forms.Form):
    period = forms.ChoiceField(choices=RANGES, label="Period")
    start_date = forms.DateField(label="From", widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(label="Through", widget=forms.DateInput(attrs={"type": "date"}))
    workflow = forms.ChoiceField(required=False, label="Workflow")
    source = forms.ChoiceField(required=False, label="Intake source",
                               choices=[("", "All sources"), *IntakeMessage.Source.choices])
    cohort = forms.ChoiceField(label="Data cohort")

    def __init__(self, data=None, *, tenant, now=None):
        self.now = now or timezone.now()
        self.today = timezone.localtime(self.now).date()
        values = {"period": "30", "workflow": "", "source": "",
                  "cohort": "demo" if settings.LEXFLOW_DEMO_MODE else "live"}
        if data is not None:
            values.update({key: data[key] for key in values.keys() | {"start_date", "end_date"} if key in data})
        if values["period"] in {"7", "14", "30", "90"}:
            values["start_date"] = (self.today - timedelta(days=int(values["period"]) - 1)).isoformat()
            values["end_date"] = self.today.isoformat()
        super().__init__(values)
        self.fields["workflow"].choices = [
            ("", "All workflows"), ("@unclassified", "Needs classification"),
            ("@unprocessed", "No case created"),
            *MatterType.objects.filter(tenant=tenant).values_list("key", "name"),
        ]
        cohorts = [("live", "Live office data")]
        if settings.LEXFLOW_DEMO_MODE:
            cohorts.insert(0, ("demo", "Synthetic demonstration"))
        self.fields["cohort"].choices = cohorts
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-select form-select-sm" if isinstance(field, forms.ChoiceField) else "form-control form-control-sm"

    def clean(self):
        data = super().clean()
        start, end = data.get("start_date"), data.get("end_date")
        if start and end:
            if start > end:
                self.add_error("end_date", "End date must be on or after the start date.")
            elif (end - start).days >= 366:
                self.add_error("end_date", "Choose a period of at most 366 days.")
            elif (start - date.min).days < (end - start).days + 1:
                self.add_error("start_date", "Choose a later start date to allow a previous-period comparison.")
            if end > self.today:
                self.add_error("end_date", "End date cannot be in the future.")
        return data

    def scope(self):
        data = self.cleaned_data
        return AnalyticsScope(data["start_date"], data["end_date"], data["cohort"] == "demo",
                              data["workflow"], data["source"], data["period"])


@dataclass(frozen=True)
class AnalyticsScope:
    start: date
    end: date
    demo: bool
    workflow: str = ""
    source: str = ""
    period: str = "custom"

    @property
    def days(self):
        return (self.end - self.start).days + 1

    @property
    def previous(self):
        return replace(self, start=self.start - timedelta(days=self.days), end=self.start - timedelta(days=1))

    def parameters(self, **overrides):
        return {"period": self.period, "start_date": self.start.isoformat(), "end_date": self.end.isoformat(),
                "cohort": "demo" if self.demo else "live", "workflow": self.workflow, "source": self.source, **overrides}

    def url(self, *, section="analytics-summary", **overrides):
        return reverse("core:dashboard") + "?" + urlencode(self.parameters(**overrides)) + "#" + section

    def intakes(self, tenant, now):
        zone = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(self.start, time.min), zone)
        stop = timezone.make_aware(datetime.combine(self.end + timedelta(days=1), time.min), zone)
        qs = IntakeMessage.objects.filter(tenant=tenant, is_demo=self.demo, received_at__gte=start,
                                         received_at__lt=stop, received_at__lte=now)
        if self.source:
            qs = qs.filter(source=self.source)
        if self.workflow == "@unprocessed":
            qs = qs.filter(matter__isnull=True)
        elif self.workflow:
            qs = qs.filter(matter__tenant=tenant, matter__is_demo=self.demo)
            if self.workflow == "@unclassified":
                qs = qs.filter(matter__matter_type__isnull=True)
            else:
                qs = qs.filter(matter__matter_type__key=self.workflow)
        return qs


def percent(numerator, denominator):
    return round(100 * numerator / denominator, 1) if denominator else None


def number(value):
    return "—" if value is None else f"{value:,.1f}".rstrip("0").rstrip(".")


def duration(value):
    if value is None:
        return "—"
    if value < 1:
        return f"{number(value * 60)} sec"
    if value < 60:
        return f"{number(value)} min"
    if value < 1440:
        return f"{number(value / 60)} hr"
    return f"{number(value / 1440)} days"


def elapsed(end, start, now):
    if start is None or end is None or end < start or end > now:
        return None
    return (end - start).total_seconds() / 60


def timing(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return {"n": 0, "median": None, "mean": None, "p90": None, "median_display": "—", "p90_display": "—"}
    position = (len(values) - 1) * .9
    lower = int(position)
    p90 = values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (position - lower)
    return {"n": len(values), "median": round(median(values), 2), "mean": round(mean(values), 2),
            "p90": round(p90, 2), "median_display": duration(median(values)), "p90_display": duration(p90)}


def _cohort(tenant, scope, now):
    intake_qs = scope.intakes(tenant, now)
    matter_qs = Matter.objects.filter(tenant=tenant, is_demo=scope.demo, message__in=intake_qs)
    intakes = list(intake_qs.values("id", "ref", "source", "status", "received_at", "processed_at"))
    matters = list(matter_qs.values("id", "ref", "message_id", "matter_type_id", "matter_type__key", "matter_type__name",
                                  "status", "created_at", "approved_at", "approved_by_id", "approval_hash",
                                  "initial_metrics", "corrected_keys"))
    fields = list(ExtractedField.objects.filter(matter__in=matter_qs).annotate(
        has_value=Case(When(value="", then=Value(False)), default=Value(True), output_field=BooleanField()),
    ).values("matter_id", "definition_id", "definition__key", "definition__label", "definition__matter_type__name",
             "definition__always_review", "status", "initial_status", "confidence", "method", "has_value"))
    return intake_qs, matter_qs, intakes, matters, fields


def _initial_totals(matters, fields):
    by_matter = defaultdict(list)
    for field in fields:
        by_matter[field["matter_id"]].append(field)
    totals = Counter(total=0, found=0, auto=0, low=0, legacy=0)
    for matter in matters:
        initial = matter["initial_metrics"]
        if not initial or not {"total", "found", "auto", "low"} <= initial.keys():
            group = by_matter[matter["id"]]
            initial = {"total": len(group), "found": sum(f["has_value"] for f in group),
                       "auto": sum(f["initial_status"] == "auto" and f["has_value"] for f in group),
                       "low": sum(f["initial_status"] in ("conflict", "missing") or
                                  (f["initial_status"] == "review" and not f["definition__always_review"]) for f in group)}
            totals["legacy"] += bool(group)
        for key in ("total", "found", "auto", "low"):
            totals[key] += initial[key]
    return totals


def needs_review(field):
    return field["status"] in ExtractedField.OPEN or (
        field["definition__always_review"] and field["has_value"] and field["status"] not in ("confirmed", "corrected"))


def _chart(key, title, hint, labels, series, *, kind="bar", links=None):
    return {"key": key, "id": "chart-" + key, "title": title, "hint": hint, "labels": labels,
            "series": series, "kind": kind, "links": links or [],
            "rows": [{"label": label, "values": [entry["data"][i] for entry in series]} for i, label in enumerate(labels)],
            "empty": not any(value for entry in series for value in entry["data"])}


def _change(current, previous, *, unit="", timed=False):
    if current is None or previous is None:
        return "No previous-period measurement"
    if timed:
        return "Previous cohort: " + duration(previous)
    if unit == "%":
        return f"{current - previous:+.1f} percentage points vs previous cohort"
    difference = current - previous
    if not previous:
        return f"Previous cohort: 0; change {difference:+,}"
    return f"{difference:+,} ({100 * difference / previous:+.1f}%) vs previous cohort"


def build_report(tenant, scope, *, now=None, chain=None):
    now = now or timezone.now()
    chain = chain or verify_chain(tenant)
    intake_qs, matter_qs, intakes, matters, fields = _cohort(tenant, scope, now)
    _, _, previous_intakes, previous_matters, previous_fields = _cohort(tenant, scope.previous, now)
    first, previous_first = _initial_totals(matters, fields), _initial_totals(previous_matters, previous_fields)
    coverage = percent(first["found"], first["total"])
    previous_coverage = percent(previous_first["found"], previous_first["total"])
    approval_time = timing(elapsed(m["approved_at"], m["created_at"], now) for m in matters)
    previous_approval = timing(elapsed(m["approved_at"], m["created_at"], now) for m in previous_matters)
    processing_time = timing(elapsed(m["processed_at"], m["received_at"], now) for m in intakes)
    attachments = list(Attachment.objects.filter(tenant=tenant, message__in=intake_qs, kind="file").values(
        "message_id", "text_method", "page_count", "reviewed_at", "size"))
    transfers = list(Transfer.objects.filter(tenant=tenant, matter__in=matter_qs, created_at__lte=now).values(
        "ref", "matter_id", "connector_id", "connector__name", "connector__delivery", "status", "created_at",
        "acknowledged_at", "delivery_attempts", "approval_hash"))
    by_matter = {m["id"]: m for m in matters}
    by_message = {m["message_id"]: m for m in matters}
    fields_by_matter = defaultdict(list)
    for field in fields:
        fields_by_matter[field["matter_id"]].append(field)

    # One proposal per field; human-confirmed/corrected values do not inflate
    # the extraction confidence distribution, nor do duplicate candidates.
    confidence_counts = [0] * 6
    for field in fields:
        if not field["has_value"] or field["status"] in ("confirmed", "corrected") or field["method"] == "human":
            continue
        confidence = float(field["confidence"]) * 100
        for index, (low, high) in enumerate(((0, 50), (50, 70), (70, 80), (80, 90), (90, 95), (95, 101))):
            if low <= confidence < high:
                confidence_counts[index] += 1
                break

    field_status = Counter(f["status"] for f in fields)
    changed = sum(f["definition__key"] in by_matter[f["matter_id"]]["corrected_keys"] for f in fields)
    field_rows = {}
    for field in fields:
        row = field_rows.setdefault(field["definition_id"], {
            "label": field["definition__label"], "workflow": field["definition__matter_type__name"],
            "total": 0, "found": 0, "conflicts": 0, "missing": 0, "review": 0, "changed": 0, "open": 0,
        })
        row["total"] += 1
        row["found"] += field["has_value"]
        row["conflicts"] += field["status"] == "conflict"
        row["missing"] += field["status"] == "missing"
        row["review"] += field["status"] == "review"
        row["open"] += needs_review(field)
        row["changed"] += field["definition__key"] in by_matter[field["matter_id"]]["corrected_keys"]
    for row in field_rows.values():
        row["coverage"] = percent(row["found"], row["total"])

    file_methods = Counter(a["text_method"] for a in attachments)
    scan_total = sum(a["text_method"] in SCAN_METHODS for a in attachments)
    pending_scans = Counter(a["message_id"] for a in attachments if a["text_method"] in SCAN_METHODS and not a["reviewed_at"])
    methods = [{"label": METHOD_LABELS.get(key, key), "count": value} for key, value in sorted(file_methods.items())]
    transfer_status = Counter(t["status"] for t in transfers)
    delivered_cases = {t["matter_id"] for t in transfers if t["status"] == "delivered"}
    closed = transfer_status["delivered"] + transfer_status["failed"]
    receipt_time = timing(elapsed(t["acknowledged_at"], t["created_at"], now) for t in transfers if t["status"] == "delivered")
    pending_transfers, connector_rows = [], {}
    superseded = 0
    for transfer in transfers:
        row = connector_rows.setdefault(transfer["connector_id"], {
            "label": transfer["connector__name"], "total": 0, "delivered": 0, "pending": 0,
            "failed": 0, "attempts": 0, "retries": 0, "times": [],
        })
        row["total"] += 1
        row["delivered"] += transfer["status"] == "delivered"
        row["failed"] += transfer["status"] == "failed"
        row["attempts"] += transfer["delivery_attempts"]
        row["retries"] += max(0, transfer["delivery_attempts"] - 1)
        if transfer["status"] == "delivered":
            row["times"].append(elapsed(transfer["acknowledged_at"], transfer["created_at"], now))
        if transfer["status"] in ("exported", "pending"):
            row["pending"] += 1
            matter = by_matter[transfer["matter_id"]]
            if not matter["approval_hash"] or transfer["approval_hash"] != matter["approval_hash"]:
                superseded += 1
            else:
                age = elapsed(now, transfer["created_at"], now)
                pending_transfers.append({"ref": transfer["ref"], "matter": matter["ref"], "connector": row["label"],
                                          "age": age, "age_display": duration(age),
                                          "url": reverse("matters:review", args=[matter["ref"]]) + "#transfer"})
    for row in connector_rows.values():
        row["receipt_rate"] = percent(row["delivered"], row["total"])
        row["median"] = timing(row.pop("times"))["median_display"]

    event_actions = defaultdict(set)
    event_qs = AuditEvent.objects.filter(tenant=tenant, object_type="matter", object_ref__in=matter_qs.values("ref"))
    for ref, action in event_qs.order_by().values_list("object_ref", "action").distinct():
        event_actions[ref].add(action)
    approved = [m for m in matters if m["approved_at"] is not None]
    complete = 0
    for matter in approved:
        required = {"matter.classified", "matter.extracted", "matter.approved"}
        if matter["status"] == "transferred":
            required.add("matter.transferred")
        complete += bool(matter["approval_hash"]) and required <= event_actions[matter["ref"]]
    audit_coverage = percent(complete, len(approved)) if chain.ok else (0 if approved else None)
    reviewers = {}
    actions = event_qs.filter(action__in=["field.confirmed", "field.corrected", "field.conflict_resolved"]).order_by().values(
        "actor_id", "actor__first_name", "actor__last_name", "actor__username", "action").annotate(count=Count("id"))
    for action in actions:
        if action["actor_id"] is None:
            continue
        name = " ".join(filter(None, [action["actor__first_name"], action["actor__last_name"]])) or action["actor__username"]
        row = reviewers.setdefault(action["actor_id"], {"name": name, "confirmed": 0, "corrected": 0, "conflicts": 0, "total": 0})
        key = {"field.confirmed": "confirmed", "field.corrected": "corrected", "field.conflict_resolved": "conflicts"}[action["action"]]
        row[key] += action["count"]
        row["total"] += action["count"]

    source_counts, states, workflows = Counter(), Counter(), {}
    dates = [scope.start + timedelta(days=i) for i in range(scope.days)]
    daily = {day: Counter() for day in dates}
    aging = Counter()
    work_items = []
    for intake in intakes:
        source_counts[intake["source"]] += 1
        daily[timezone.localtime(intake["received_at"]).date()][intake["status"]] += 1
        matter = by_message.get(intake["id"])
        status = matter["status"] if matter else ("processing_failed" if intake["status"] == "failed" else "unprocessed")
        states[status] += 1
        name = matter["matter_type__name"] if matter and matter["matter_type_id"] else ("Needs classification" if matter else "No case created")
        key = matter["matter_type__key"] if matter and matter["matter_type_id"] else ("@unclassified" if matter else "@unprocessed")
        workflow = workflows.setdefault(key, {"label": name, "intakes": 0, "cases": 0, "open": 0, "approved": 0,
                                              "delivered": 0, "times": [], "url": scope.url(workflow=key)})
        workflow["intakes"] += 1
        workflow["cases"] += bool(matter)
        workflow["approved"] += bool(matter and matter["approved_at"])
        workflow["delivered"] += bool(matter and matter["id"] in delivered_cases)
        current_fields = fields_by_matter[matter["id"]] if matter else []
        open_fields = sum(needs_review(f) for f in current_fields)
        is_open = not matter or matter["status"] != "transferred"
        age = elapsed(now, intake["received_at"], now)
        if is_open:
            workflow["open"] += 1
            aging[0 if age < 1440 else 1 if age < 4320 else 2 if age < 10080 else 3] += 1
        if matter:
            workflow["times"].append(elapsed(matter["approved_at"], matter["created_at"], now))
        work_items.append({
            "ref": matter["ref"] if matter else intake["ref"], "workflow": name, "status": status,
            "status_label": dict(Matter.Status.choices).get(status, {"unprocessed": "Awaiting processing", "processing_failed": "Processing failed"}.get(status, status)),
            "source": dict(IntakeMessage.Source.choices).get(intake["source"], intake["source"]),
            "received_at": intake["received_at"], "age": age, "age_display": duration(age), "is_open": is_open,
            "open_fields": open_fields, "scan_reviews": pending_scans[intake["id"]],
            "url": reverse("matters:review", args=[matter["ref"]]) if matter else reverse("intake:inbox") + "?m=" + intake["ref"],
        })
    for row in workflows.values():
        row["median"] = timing(row.pop("times"))["median_display"]
    work_items.sort(key=lambda row: (not row["is_open"], row["received_at"]))
    workflow_rows = sorted(workflows.values(), key=lambda row: (-row["intakes"], row["label"]))
    source_rows = [{"label": label, "count": source_counts[value], "share": percent(source_counts[value], len(intakes)),
                    "url": scope.url(source=value)} for value, label in IntakeMessage.Source.choices]
    state_choices = [("unprocessed", "Awaiting processing"), ("processing_failed", "Processing failed"), *Matter.Status.choices]
    review_cases = sum(m["status"] in ("needs_review", "unclassified", "ready") for m in matters)
    cards = [
        {"key": "intakes", "label": "Intakes received", "value": len(intakes), "display": f"{len(intakes):,}",
         "hint": _change(len(intakes), len(previous_intakes)), "unit": "intakes", "denominator": None},
        {"key": "coverage", "label": "Initial extraction coverage", "value": coverage, "display": number(coverage) + ("%" if coverage is not None else ""),
         "hint": f"{first['found']:,} / {first['total']:,} expected fields; " + _change(coverage, previous_coverage, unit="%"), "unit": "%", "denominator": first["total"]},
        {"key": "review", "label": "Awaiting review or approval", "value": review_cases, "display": f"{review_cases:,}",
         "hint": "Needs classification, field review or notary approval", "unit": "cases", "denominator": len(matters)},
        {"key": "approval", "label": "Median time to approval", "value": approval_time["median"], "display": approval_time["median_display"],
         "hint": f"{approval_time['n']:,} completed cases; " + _change(approval_time["median"], previous_approval["median"], timed=True), "unit": "minutes", "denominator": approval_time["n"]},
        {"key": "delivered", "label": "Cases with a verified receipt", "value": len(delivered_cases), "display": f"{len(delivered_cases):,}",
         "hint": f"{transfer_status['delivered']:,} delivered exports across {len(matters):,} cases", "unit": "cases", "denominator": len(matters)},
        {"key": "pending", "label": "Current exports awaiting receipt", "value": len(pending_transfers), "display": f"{len(pending_transfers):,}",
         "hint": f"{superseded:,} superseded pending exports tracked separately", "unit": "exports", "denominator": len(transfers)},
    ]
    charts = {
        "daily": _chart("daily", "Intakes by day", "Received in the selected period, grouped by current processing status.",
            [day.strftime("%d %b") for day in dates], [{"name": label, "data": [daily[day][key] for day in dates]}
                for key, label in (("processed", "Processed"), ("new", "Awaiting processing"), ("failed", "Processing failed"))], kind="stacked"),
        "conf": _chart("conf", "Unreviewed proposal confidence", "One current proposal per field. Human-reviewed values excluded; scores are heuristic, not measured accuracy.",
            ["<50", "50–69", "70–79", "80–89", "90–94", "95–100"], [{"name": "Fields", "data": confidence_counts}]),
        "states": _chart("states", "Current intake and case status", "One current state per intake; all selected intakes are included.",
            [label for _, label in state_choices], [{"name": "Intakes", "data": [states[key] for key, _ in state_choices]}], kind="horizontal"),
        "sources": _chart("sources", "Intake sources", "Select a source in the chart or table to narrow the report.",
            [row["label"] for row in source_rows], [{"name": "Intakes", "data": [row["count"] for row in source_rows]}],
            kind="donut", links=[row["url"] for row in source_rows]),
        "fields": _chart("fields", "Current field review state", "Current state after review and re-extraction; distinct from first-extraction coverage.",
            [label for _, label in ExtractedField.Status.choices], [{"name": "Fields", "data": [field_status[key] for key, _ in ExtractedField.Status.choices]}], kind="horizontal"),
        "aging": _chart("aging", "Age of unfinished intakes", "Time since receipt for cases not yet transferred, including unprocessed and failed intakes. These are age bands, not agreed SLAs.",
            ["Under 1 day", "1–3 days", "3–7 days", "7+ days"], [{"name": "Open intakes", "data": [aging[i] for i in range(4)]}]),
        "deliveries": _chart("deliveries", "Export outcomes", "Export records, including superseded approvals. Export creation does not confirm delivery.",
            [label for _, label in Transfer.Status.choices], [{"name": "Exports", "data": [transfer_status[key] for key, _ in Transfer.Status.choices]}], kind="horizontal"),
    }
    has_baseline = bool(tenant.baseline_recorded_at) or scope.demo
    quality = {"expected": first["total"], "found": first["found"], "coverage": coverage,
               "prefilled": percent(first["auto"], first["total"]), "exceptions": percent(first["low"], first["total"]),
               "current_fields": len(fields), "changed": changed, "correction_rate": percent(changed, len(fields)),
               "conflicts": field_status["conflict"], "missing": field_status["missing"], "policy_review": field_status["review"],
               "scan_documents": scan_total, "scan_reviews": sum(pending_scans.values()),
               "legacy_cases": first["legacy"]}
    return {
        "scope": scope, "generated_at": now, "timezone": str(timezone.get_current_timezone()),
        "query": urlencode(scope.parameters()), "previous_start": scope.previous.start, "previous_end": scope.previous.end,
        "intake_count": len(intakes), "matter_count": len(matters), "cards": cards, "quality": quality,
        "charts": charts, "workflows": workflow_rows, "sources": source_rows,
        "field_rows": sorted(field_rows.values(), key=lambda row: (-row["open"], row["workflow"], row["label"])),
        "documents": {"files": len(attachments), "pages": sum(a["page_count"] for a in attachments),
                      "unknown_pages": sum(not a["page_count"] for a in attachments),
                      "megabytes": round(sum(a["size"] for a in attachments) / 1048576, 2), "methods": methods},
        "timings": [{"label": "Intake to processing", **processing_time}, {"label": "Case creation to approval", **approval_time},
                    {"label": "Export to confirmed receipt", **receipt_time}],
        "transfer": {"total": len(transfers), "delivered": transfer_status["delivered"], "failed": transfer_status["failed"],
                     "pending": transfer_status["exported"] + transfer_status["pending"], "actionable": len(pending_transfers),
                     "superseded": superseded, "receipt_rate": percent(transfer_status["delivered"], len(transfers)),
                     "closed_rate": percent(transfer_status["delivered"], closed), "closed": closed,
                     "attempts": sum(t["delivery_attempts"] for t in transfers),
                     "retries": sum(max(0, t["delivery_attempts"] - 1) for t in transfers)},
        "connectors": sorted(connector_rows.values(), key=lambda row: row["label"]),
        "pending_transfers": sorted(pending_transfers, key=lambda row: -row["age"]),
        "reviewers": sorted(reviewers.values(), key=lambda row: (-row["total"], row["name"])),
        "work_items": work_items, "open_count": sum(row["is_open"] for row in work_items),
        "audit": {"ok": chain.ok, "checked": chain.checked, "complete": complete if chain.ok else 0,
                  "approved": len(approved), "coverage": audit_coverage},
        "baseline": {"available": has_baseline, "illustrative": scope.demo, "recorded_at": tenant.baseline_recorded_at,
                     "minutes": tenant.baseline_minutes_per_matter if has_baseline else None,
                     "errors": tenant.baseline_rekey_error_pct if has_baseline else None,
                     "audit": tenant.baseline_audit_completeness_pct if has_baseline else None},
    }


def csv_rows(report):
    """Aggregate values only: never document text, case values or receipt secrets."""
    scope = report["scope"]
    yield ["Section", "Metric", "Group", "Value", "Unit", "Denominator"]
    for label, value in (("Data cohort", "synthetic" if scope.demo else "live"), ("Received from", scope.start),
                         ("Received through", scope.end), ("Workflow filter", scope.workflow or "all"),
                         ("Source filter", scope.source or "all"), ("Generated at", report["generated_at"].isoformat()),
                         ("Reporting timezone", report["timezone"]),
                         ("Scope", "Intakes received during the period; current outcomes include later reviews and receipts")):
        yield ["Scope", label, "", value, "", ""]
    for card in report["cards"]:
        yield ["Summary", card["label"], "", card["value"], card["unit"], card["denominator"]]
    for key, value in report["quality"].items():
        yield ["Quality", key, "", value, "percent" if key in ("coverage", "prefilled", "exceptions", "correction_rate") else "count", ""]
    for item in report["timings"]:
        for key in ("median", "mean", "p90"):
            yield ["Timing", key, item["label"], item[key], "minutes", item["n"]]
    for chart in report["charts"].values():
        for row in chart["rows"]:
            for series, value in zip(chart["series"], row["values"]):
                yield [chart["title"], series["name"], row["label"], value, "count", ""]
    for section, key, columns in (
        ("Workflow", "workflows", ("intakes", "cases", "open", "approved", "delivered")),
        ("Connector", "connectors", ("total", "delivered", "pending", "failed", "attempts", "retries", "receipt_rate")),
        ("Field", "field_rows", ("total", "found", "conflicts", "missing", "review", "changed", "coverage")),
    ):
        for row in report[key]:
            group = (row["workflow"] + " / " if key == "field_rows" else "") + row["label"]
            for column in columns:
                yield [section, column, group, row[column], "%" if column in ("coverage", "receipt_rate") else "count", ""]
    for key, value in report["transfer"].items():
        yield ["Delivery", key, "", value, "%" if key.endswith("rate") else "count", ""]
    for key in ("files", "pages", "unknown_pages", "megabytes"):
        yield ["Documents", key, "", report["documents"][key], "MiB" if key == "megabytes" else "count", ""]
    for row in report["documents"]["methods"]:
        yield ["Documents", "reading method", row["label"], row["count"], "files", ""]
    for row in report["reviewers"]:
        for key in ("confirmed", "corrected", "conflicts", "total"):
            yield ["Review activity", key, row["name"], row[key], "actions", ""]
    for key in ("minutes", "errors", "audit"):
        yield ["Baseline", key, "illustrative" if report["baseline"]["illustrative"] else "recorded", report["baseline"][key], "minutes" if key == "minutes" else "%", ""]
    for key in ("complete", "approved", "coverage", "ok"):
        yield ["Audit", key, "", report["audit"][key], "%" if key == "coverage" else "count", ""]


def safe_csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, str) and (value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))):
        return "'" + value
    return value
