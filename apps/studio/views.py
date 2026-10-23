import re
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.core.exceptions import ValidationError
from apps.core.models import Tenant
from .validation import validate_paths
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.audit.models import AuditEvent
from apps.audit.services import record
from apps.core.decorators import ADMINS, tenant_view

from .defaults import MATTER_TYPES
from .models import DocumentType, FieldDefinition, MatterType

PATH_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")


@tenant_view()
def index(request):
    t = request.tenant
    types = MatterType.objects.filter(tenant=t).annotate(n_fields=Count("fields", distinct=True),
                                                       n_docs=Count("document_types", distinct=True),
                                                       n_matters=Count("matter", distinct=True))
    learned = AuditEvent.objects.filter(tenant=t, action="studio.anchor_learned").order_by("-seq")[:8]
    return render(request, "studio/index.html", {"types": types, "learned": learned, "nav": "studio"})


@tenant_view()
def matter_type(request, key):
    t = request.tenant
    mt = get_object_or_404(MatterType, tenant=t, key=key)
    defaults = next((s for s in MATTER_TYPES if s["key"] == mt.key), None)
    default_anchors = {f[0]: set(f[4]) for f in defaults["fields"]} if defaults else {}
    fields = list(mt.fields.all())
    for f in fields:
        base = default_anchors.get(f.key, set())
        f.anchor_rows = [(a, a not in base) for a in f.anchors]
    return render(request, "studio/matter_type.html", {
        "mt": mt, "fields": fields, "doc_types": DocumentType.objects.filter(tenant=t, matter_type=mt),
        "data_types": FieldDefinition.DataType.choices, "nav": "studio",
    })


def _back(mt, anchor=""):
    return redirect(reverse("studio:matter_type", args=[mt.key]) + (f"#{anchor}" if anchor else ""))


@require_POST
@tenant_view(roles=ADMINS)
@transaction.atomic
def save_field(request, key, pk):
    mt = get_object_or_404(MatterType, tenant=request.tenant, key=key)
    f = get_object_or_404(FieldDefinition, matter_type=mt, pk=pk)
    Tenant.objects.select_for_update().get(pk=request.tenant.pk)
    path = request.POST.get("destination_path", "").strip()
    try:
        validate_paths([path] + list(mt.fields.exclude(pk=f.pk).values_list("destination_path", flat=True)))
    except ValidationError as exc:
        for message in exc.messages:
            messages.error(request, message)
        return _back(mt, f"f-{f.key}")
    if not PATH_RE.match(path):
        messages.error(request, "Destination must look like Section.Field, for example Beteiligter.Kaeufer.Name.")
        return _back(mt, f"f-{f.key}")
    try:
        threshold = Decimal(request.POST.get("auto_threshold", "0.90"))
    except InvalidOperation:
        threshold = f.auto_threshold
    if not threshold.is_finite():
        messages.error(request, "The confidence threshold must be finite.")
        return _back(mt, f"f-{f.key}")
    threshold = max(Decimal("0.50"), min(Decimal("0.99"), threshold))
    before = {"path": f.destination_path, "threshold": str(f.auto_threshold), "required": f.required,
              "always_review": f.always_review}
    f.label = request.POST.get("label", f.label).strip()[:80] or f.label
    f.destination_path = path
    f.auto_threshold = threshold
    f.required = request.POST.get("required") == "on"
    f.always_review = request.POST.get("always_review") == "on"
    f.save()
    record(request.tenant, "studio.field_changed", f"Changed rule for {f.label}", actor=request.user,
           object_type="fielddefinition", object_ref=f"{mt.key}.{f.key}",
           details={"before": before, "after": {"path": f.destination_path, "threshold": str(f.auto_threshold),
                                                "required": f.required, "always_review": f.always_review}})
    messages.success(request, f"Saved {f.label}. New intakes use this rule.")
    return _back(mt, f"f-{f.key}")


@require_POST
@tenant_view(roles=ADMINS)
def anchors(request, key, pk):
    mt = get_object_or_404(MatterType, tenant=request.tenant, key=key)
    f = get_object_or_404(FieldDefinition, matter_type=mt, pk=pk)
    label = request.POST.get("label", "").strip().rstrip(":")
    if request.POST.get("op") == "remove":
        if len(f.anchors) <= 1:
            messages.error(request, "Keep at least one label so LexFlow can find this field.")
            return _back(mt, f"f-{f.key}")
        f.anchors = [a for a in f.anchors if a != label]
        verb = "Removed"
    else:
        if not label or len(label) > 40:
            messages.error(request, "Labels are 1–40 characters.")
            return _back(mt, f"f-{f.key}")
        if label.casefold() not in [a.casefold() for a in f.anchors]:
            f.anchors = f.anchors + [label]
        verb = "Added"
    f.save(update_fields=["anchors"])
    record(request.tenant, "studio.anchor_changed", f"{verb} label “{label}” for {f.label}", actor=request.user,
           object_type="fielddefinition", object_ref=f"{mt.key}.{f.key}", details={"label": label, "op": verb.lower()})
    return _back(mt, f"f-{f.key}")


@require_POST
@tenant_view(roles=ADMINS)
def save_doctype(request, key, pk):
    mt = get_object_or_404(MatterType, tenant=request.tenant, key=key)
    dt = get_object_or_404(DocumentType, tenant=request.tenant, matter_type=mt, pk=pk)
    words = [w.strip() for w in request.POST.get("keywords", "").split(",") if w.strip()]
    if not words:
        messages.error(request, "Add at least one identifying phrase.")
        return _back(mt, "documents")
    dt.keywords = words[:20]
    dt.save(update_fields=["keywords"])
    record(request.tenant, "studio.doctype_changed", f"Changed phrases for {dt.name}", actor=request.user,
           object_type="documenttype", object_ref=dt.key, details={"keywords": dt.keywords})
    messages.success(request, f"Saved phrases for {dt.name}.")
    return _back(mt, "documents")


@require_POST
@tenant_view(roles=ADMINS)
def toggle_type(request, key):
    mt = get_object_or_404(MatterType, tenant=request.tenant, key=key)
    if mt.is_active and MatterType.objects.filter(tenant=request.tenant, is_active=True).count() == 1:
        raise PermissionDenied("Keep at least one active workflow.")
    mt.is_active = not mt.is_active
    mt.save(update_fields=["is_active"])
    record(request.tenant, "studio.workflow_toggled", f"{'Enabled' if mt.is_active else 'Paused'} {mt.name}",
           actor=request.user, object_type="mattertype", object_ref=mt.key)
    return _back(mt)
