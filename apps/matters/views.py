from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from apps.audit.models import AuditEvent
from apps.connectors.models import Connector
from apps.core.decorators import tenant_view
from apps.studio.models import MatterType

from . import services
from .models import ExtractedField, FieldCandidate, Matter

STAGES = ["Receive", "Classify", "Extract", "Validate", "Review", "Transfer", "Audit"]


def render_marked(text, spans):
    """Escape document text and wrap known spans in <mark> without altering characters."""
    out, pos = [], 0
    for start, end, key, tone, title in sorted(spans, key=lambda s: (s[0], -(s[1] - s[0]))):
        if start < pos or start >= end or end > len(text):
            continue
        out.append(escape(text[pos:start]))
        out.append(f'<mark class="lf-mark lf-mark--{tone}" data-field="{key}" title="{escape(title)}">')
        out.append(escape(text[start:end]))
        out.append("</mark>")
        pos = end
    out.append(escape(text[pos:]))
    return mark_safe("".join(out))


def _flash_errors(request, exc):
    for msg in getattr(exc, "messages", [str(exc)]):
        messages.error(request, msg)


@tenant_view()
def matter_list(request):
    t = request.tenant
    qs = Matter.objects.filter(tenant=t).select_related("matter_type", "message", "approved_by").annotate(
        n_fields=Count("fields", filter=~Q(fields__value="")),
        n_auto=Count("fields", filter=Q(fields__initial_status="auto") & ~Q(fields__value="")),
        n_conflicts=Count("fields", filter=Q(fields__status="conflict")),
        n_open=Count("fields", filter=Q(fields__status__in=["review", "missing"])),
    )
    status = request.GET.get("status", "")
    if status in Matter.Status.values:
        qs = qs.filter(status=status)
    q = request.GET.get("q", "").strip()
    if q:
        needle = q.casefold()
        ids = [m.pk for m in qs.select_related("tenant") if needle in m.ref.casefold() or needle in m.title.casefold()]
        qs = qs.filter(pk__in=ids)
    counts = dict(Matter.objects.filter(tenant=t).values_list("status").annotate(n=Count("id")))
    tabs = [("", "All", sum(counts.values()))] + [(v, label, counts.get(v, 0)) for v, label in Matter.Status.choices]
    return render(request, "matters/list.html", {"matters": qs[:200], "tabs": tabs, "status": status, "q": q,
                                                 "nav": "matters"})


@tenant_view()
def review(request, ref):
    t = request.tenant
    matter = get_object_or_404(Matter.objects.select_related("matter_type", "message", "approved_by"), tenant=t, ref=ref)
    fields = list(
        matter.fields.select_related("definition", "source", "reviewed_by").prefetch_related(
            Prefetch("candidates", queryset=FieldCandidate.objects.select_related("source"))
        )
    )
    attachments = [a for a in matter.message.attachments.select_related("doc_type") if a.text or a.kind == "file"]
    spans = {a.pk: [] for a in attachments}
    for f in fields:
        if f.status == ExtractedField.Status.CONFLICT:
            for c in f.candidates.all():
                spans.setdefault(c.source_id, []).append(
                    (c.span_start, c.span_end, f.definition.key, "seal", f"{f.definition.label}: {c.display_value}"))
        elif f.source_id and f.span_start is not None and f.span_end:
            spans.setdefault(f.source_id, []).append(
                (f.span_start, f.span_end, f.definition.key, f.tone, f"{f.definition.label} · {f.confidence_pct}%"))
    docs = []
    for a in attachments:
        docs.append({"att": a, "html": render_marked(a.text, spans.get(a.pk, [])), "marks": len(spans.get(a.pk, []))})
    try:
        active = int(request.GET.get("doc", 0))
    except ValueError:
        active = 0
    if not any(d["att"].pk == active for d in docs):
        withmarks = [d for d in docs if d["marks"] and d["att"].kind == "file"]
        active = (withmarks or docs)[0]["att"].pk if docs else 0

    open_fields = [f for f in fields if f.is_open]
    return render(request, "matters/review.html", {
        "matter": matter,
        "fields": fields,
        "open_fields": open_fields,
        "docs": docs,
        "active_doc": active,
        "blockers": services.approval_blockers(matter),
        "connectors": sorted(Connector.objects.filter(tenant=t, is_active=True),
                             key=lambda c: ["xml", "json", "csv"].index(c.kind)),
        "transfers": matter.transfers.select_related("connector", "created_by"),
        "events": AuditEvent.objects.filter(tenant=t, object_ref=matter.ref).order_by("seq"),
        "matter_types": MatterType.objects.filter(tenant=t, is_active=True),
        "stages": STAGES,
        "nav": "matters",
    })


@require_POST
@tenant_view()
def field_action(request, ref, field_id):
    matter = get_object_or_404(Matter, tenant=request.tenant, ref=ref)
    field = get_object_or_404(ExtractedField.objects.select_related("definition", "matter"), matter=matter, pk=field_id)
    action = request.POST.get("action")
    try:
        if action == "confirm":
            services.confirm_field(field, request.user)
            messages.success(request, f"Confirmed {field.definition.label}.")
        elif action == "correct":
            services.correct_field(field, request.user, request.POST.get("value", ""))
            messages.success(request, f"Saved {field.definition.label}.")
        elif action == "choose":
            cand = get_object_or_404(FieldCandidate, field=field, pk=request.POST.get("candidate"))
            services.choose_candidate(field, request.user, cand)
            messages.success(request, f"Resolved {field.definition.label} using {cand.source.display_name}.")
        else:
            messages.error(request, "Unknown action.")
    except ValidationError as exc:
        _flash_errors(request, exc)
    return redirect(f"{matter_url(matter)}#field-{field.definition.key}")


@require_POST
@tenant_view()
def teach(request, ref):
    matter = get_object_or_404(Matter, tenant=request.tenant, ref=ref)
    att = get_object_or_404(matter.message.attachments, pk=request.POST.get("attachment"))
    field = get_object_or_404(ExtractedField.objects.select_related("definition"), matter=matter,
                              definition__key=request.POST.get("field"))
    try:
        start, end = int(request.POST.get("start")), int(request.POST.get("end"))
        learned = services.teach_from_selection(matter, request.user, att, start, end, field)
    except (TypeError, ValueError):
        messages.error(request, "Select text in the document first.")
    except ValidationError as exc:
        _flash_errors(request, exc)
    else:
        if learned:
            messages.success(request, f"Filled {field.definition.label} and learned the label “{learned}” for next time.")
        else:
            messages.success(request, f"Filled {field.definition.label} from your selection.")
    return redirect(f"{matter_url(matter)}?doc={att.pk}#field-{field.definition.key}")


@require_POST
@tenant_view()
def approve(request, ref):
    matter = get_object_or_404(Matter, tenant=request.tenant, ref=ref)
    try:
        services.approve(matter, request.membership)
        messages.success(request, "Approved. Choose a connector to transfer the data.")
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    except ValidationError as exc:
        _flash_errors(request, exc)
    return redirect(matter_url(matter) + "#transfer")


@require_POST
@tenant_view()
def set_type(request, ref):
    matter = get_object_or_404(Matter, tenant=request.tenant, ref=ref)
    mt = get_object_or_404(MatterType, tenant=request.tenant, pk=request.POST.get("matter_type"))
    try:
        services.set_matter_type(matter, request.user, mt)
        messages.success(request, f"Treated as {mt.name} and extracted again.")
    except ValidationError as exc:
        _flash_errors(request, exc)
    return redirect(matter_url(matter))


@require_POST
@tenant_view()
def reprocess(request, ref):
    matter = get_object_or_404(Matter, tenant=request.tenant, ref=ref)
    try:
        services.run_extraction(matter, actor=request.user)
        messages.success(request, "Extracted again with the current workflow rules.")
    except ValidationError as exc:
        _flash_errors(request, exc)
    return redirect(matter_url(matter))


def matter_url(matter):
    from django.urls import reverse

    return reverse("matters:review", args=[matter.ref])


@require_POST
@tenant_view()
def reopen(request, ref):
    matter = get_object_or_404(Matter, tenant=request.tenant, ref=ref)
    try:
        services.reopen(matter, request.membership)
        messages.success(request, "Reopened. Review the values before creating a new approval.")
    except (ValidationError, PermissionDenied) as exc:
        _flash_errors(request, exc)
    return redirect(matter_url(matter))


@require_POST
@tenant_view()
def acknowledge_source(request, ref, pk):
    from django.db import transaction
    from django.utils import timezone
    from apps.audit.services import record
    from apps.core.models import Tenant
    matter = get_object_or_404(Matter, tenant=request.tenant, ref=ref)
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=request.tenant.pk)
        Matter.objects.select_for_update().get(pk=matter.pk)
        matter.refresh_from_db()
        if matter.is_locked:
            return redirect(matter_url(matter))
        att = get_object_or_404(matter.message.attachments, pk=pk,
                               text_method__in=["needs_ocr", "ocr", "pdf_ocr", "ocr_partial"])
        if request.POST.get("reviewed") != "yes":
            messages.error(request, "Confirm that you read the original and checked its values.")
        else:
            att.reviewed_by, att.reviewed_at = request.user, timezone.now()
            att.save(update_fields=["reviewed_by", "reviewed_at"])
            record(request.tenant, "document.manually_reviewed", "Scanned or partially readable source checked against the original",
                   actor=request.user, obj=matter, details={"document_sha256": att.sha256})
            messages.success(request, "Source review recorded.")
    return redirect(matter_url(matter))
