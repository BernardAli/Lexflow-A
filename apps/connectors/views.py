from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.audit.services import record
from apps.core.decorators import APPROVERS, tenant_view
from apps.matters.models import Matter

from . import services
from .models import Connector, Transfer


@tenant_view()
def index(request):
    t = request.tenant
    return render(request, "connectors/index.html", {
        "connectors": Connector.objects.filter(tenant=t),
        "transfers": Transfer.objects.filter(tenant=t).select_related("matter", "connector", "created_by")[:50],
        "ladder": [
            ("Official API", "Best option", "Preferred when the vendor supplies an approved API; not implemented in this demo.", False),
            ("Official plugin / SDK", "Strong option", "A future native integration milestone; not implemented in this demo.", False),
            ("Import / export interface", "Practical option", "XML, CSV or file exchange the destination supports.", True),
            ("Browser helper", "Limited fallback", "Assists the user without bypassing authorisation.", False),
            ("UI automation / RPA", "Last resort", "Only where no documented interface exists.", False),
        ],
        "nav": "connectors",
    })


@require_POST
@tenant_view(roles=APPROVERS)
def send(request, ref):
    matter = get_object_or_404(Matter, tenant=request.tenant, ref=ref)
    connector = get_object_or_404(Connector, tenant=request.tenant, pk=request.POST.get("connector"))
    try:
        t = services.transfer(matter, connector, request.user)
        messages.success(request, f"{t.get_status_display()}: {t.field_count} fields ({t.ref}).")
    except ValidationError as exc:
        for m in exc.messages:
            messages.error(request, m)
    return redirect(reverse("matters:review", args=[matter.ref]) + "#transfer")


@tenant_view()
def preview(request, ref, pk):
    matter = get_object_or_404(Matter, tenant=request.tenant, ref=ref)
    connector = get_object_or_404(Connector, tenant=request.tenant, pk=pk)
    if matter.matter_type is None:
        return HttpResponse("Classify the matter first.", content_type="text/plain", status=400)
    try:
        body, filename, content_type, _ = services.render(connector, matter)
    except ValidationError as exc:
        return HttpResponse(" ".join(exc.messages), content_type="text/plain", status=409)
    return HttpResponse(body, content_type="text/plain; charset=utf-8")


@tenant_view()
def download(request, ref):
    tr = get_object_or_404(Transfer, tenant=request.tenant, ref=ref)
    try:
        body = services.checked_payload(tr, allow_legacy=True)
    except ValidationError as exc:
        return HttpResponse(" ".join(exc.messages), content_type="text/plain", status=409)
    record(request.tenant, "transfer.downloaded", f"Downloaded {tr.filename}", actor=request.user,
           object_type="transfer", object_ref=tr.matter.ref, details={"transfer": tr.ref})
    resp = HttpResponse(body, content_type=f"{tr.content_type}; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{tr.filename}"'
    resp["X-LexFlow-SHA256"] = tr.checksum
    if tr.signature:
        resp["X-LexFlow-Signature"] = f"sha256={tr.signature}"
    return resp


@require_POST
@tenant_view(roles=APPROVERS)
def acknowledge(request, ref):
    exported = get_object_or_404(Transfer, tenant=request.tenant, ref=ref)
    try:
        services.acknowledge(exported, request.user, checksum=request.POST.get("checksum", ""),
                             receipt_reference=request.POST.get("receipt_reference", ""))
        messages.success(request, "Destination receipt confirmed. Delivery and retention start are recorded.")
    except ValidationError as exc:
        for error in exc.messages:
            messages.error(request, error)
    return redirect(reverse("matters:review", args=[exported.matter.ref]) + "#transfer")


@require_POST
@tenant_view(roles=APPROVERS)
def demo_receive(request, ref):
    exported = get_object_or_404(Transfer, tenant=request.tenant, ref=ref)
    try:
        services.receive_demo(exported, request.user)
        messages.success(request, "Demo receiver accepted the exported XML and confirmed its checksum. This is a simulated destination.")
    except ValidationError as exc:
        for error in exc.messages:
            messages.error(request, error)
    return redirect(reverse("matters:review", args=[exported.matter.ref]) + "#transfer")


@require_POST
@tenant_view(roles=APPROVERS)
def deliver(request, ref):
    exported = get_object_or_404(Transfer, tenant=request.tenant, ref=ref)
    try:
        result = services.deliver_webhook(exported, request.user)
        if result.status == Transfer.Status.DELIVERED:
            messages.success(request, "The HTTP destination returned a verified receipt. Delivery is recorded.")
        else:
            messages.error(request, result.error)
    except ValidationError as exc:
        for error in exc.messages:
            messages.error(request, error)
    return redirect(reverse("matters:review", args=[exported.matter.ref]) + "#transfer")
