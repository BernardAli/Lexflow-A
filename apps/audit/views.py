import csv

from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render

from apps.core.decorators import tenant_view

from .models import AuditEvent
from .services import record, verify_chain

GROUPS = [
    ("", "Everything"),
    ("intake", "Intake"),
    ("matter", "Matters"),
    ("field", "Review"),
    ("studio", "Workflow studio"),
    ("security", "Security"),
    ("auth", "Sign-ins"),
]


def _filtered(request):
    qs = AuditEvent.objects.filter(tenant=request.tenant).select_related("actor", "tenant")
    group = request.GET.get("group", "")
    if group:
        qs = qs.filter(action__startswith=group + ".")
    q = request.GET.get("q", "").strip()
    if q:
        needle = q.casefold()
        ids = [event.pk for event in qs if needle in event.object_ref.casefold() or
               needle in event.summary.casefold() or needle in event.actor_label.casefold()]
        qs = qs.filter(pk__in=ids)
    return qs, group, q


@tenant_view()
def index(request):
    qs, group, q = _filtered(request)
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    return render(request, "audit/index.html", {
        "page": page, "group": group, "q": q, "groups": GROUPS, "chain": verify_chain(request.tenant), "nav": "audit",
    })


@tenant_view()
def export(request):
    qs, _, _ = _filtered(request)
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="lexflow-audit-{request.tenant.slug}.csv"'
    w = csv.writer(resp)
    w.writerow(["seq", "time", "actor", "action", "object_type", "object_ref", "summary", "prev_hash", "hash"])
    for e in qs.order_by("seq").iterator():
        w.writerow([e.seq, e.created_at.isoformat(), e.actor_label, e.action, e.object_type, e.object_ref, e.summary,
                    e.prev_hash, e.hash])
    record(request.tenant, "audit.exported", "Exported the audit trail", actor=request.user, object_type="audit",
           object_ref=request.tenant.slug)
    return resp
