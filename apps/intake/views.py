from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils.http import content_disposition_header
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from apps.audit.services import record
from apps.core.decorators import tenant_view
from apps.matters.models import Matter
from apps.matters.services import ingest_message, process_message

from .models import Attachment, IntakeMessage

from .validation import ALLOWED_EXT, read_uploads


def _pane_context(message):
    matter = Matter.objects.filter(message=message).select_related("matter_type").first()
    fields = []
    if matter:
        fields = list(matter.fields.select_related("definition").exclude(value="", initial_status="auto"))
    return {"matter": matter, "pane_fields": fields,
            "pane_conflicts": [f for f in fields if f.status == "conflict"]}


@tenant_view()
def inbox(request):
    t = request.tenant
    qs = IntakeMessage.objects.filter(tenant=t).select_related("matter")
    source = request.GET.get("source")
    if source in IntakeMessage.Source.values:
        qs = qs.filter(source=source)
    items = list(qs[:60])
    selected = None
    ref = request.GET.get("m")
    if ref:
        selected = get_object_or_404(IntakeMessage, tenant=t, ref=ref)
    elif items:
        selected = items[0]
    ctx = {"items": items, "selected": selected, "source": source, "sources": IntakeMessage.Source.choices,
           "nav": "inbox"}
    if selected:
        ctx.update(_pane_context(selected))
        ctx["files"] = selected.files
    return render(request, "intake/inbox.html", ctx)


@tenant_view()
def new_intake(request):
    if request.method == "POST":
        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "")
        source = request.POST.get("source")
        try:
            payload = read_uploads(request.FILES.getlist("files"))
            msg = ingest_message(
                request.tenant, subject=subject, body=body, files=payload,
                source=source if source in IntakeMessage.Source.values else IntakeMessage.Source.UPLOAD,
                sender_name=request.POST.get("sender_name", "").strip(),
                sender_email=request.POST.get("sender_email", "").strip(),
                actor=request.user, process=request.POST.get("process") == "on", is_demo=settings.LEXFLOW_DEMO_MODE,
            )
        except ValidationError as exc:
            for error in exc.messages:
                messages.error(request, error)
            return render(request, "intake/new.html", {"nav": "inbox", "form": request.POST})
        if msg.status == IntakeMessage.Status.FAILED:
            messages.error(request, msg.error)
        messages.success(request, f"Received {msg.ref}.")
        if hasattr(msg, "matter"):
            return redirect("matters:review", ref=msg.matter.ref)
        return redirect(f"{reverse('intake:inbox')}?m={msg.ref}")
    return render(request, "intake/new.html", {"nav": "inbox", "form": {}})


@require_POST
@tenant_view()
def process(request, ref):
    msg = get_object_or_404(IntakeMessage, tenant=request.tenant, ref=ref)
    if Matter.objects.filter(message=msg).exists():
        messages.info(request, "This message has already been processed.")
        return redirect("matters:review", ref=msg.matter.ref)
    try:
        matter = process_message(msg, actor=request.user)
    except Exception:
        messages.error(request, "Processing failed. Originals are retained; check the documents before retrying.")
        return redirect(f"{reverse('intake:inbox')}?m={msg.ref}")
    messages.success(request, f"Processed with LexFlow — {matter.fields.count()} fields extracted.")
    if request.POST.get("next") == "pane":
        return redirect("intake:taskpane", ref=msg.ref)
    return redirect("matters:review", ref=matter.ref)


@tenant_view()
def download(request, pk):
    att = get_object_or_404(Attachment, tenant=request.tenant, pk=pk, kind=Attachment.Kind.FILE)
    if att.text_method == "purged":
        return HttpResponse("This source was removed under the retention policy.", status=410)
    try:
        data = att.read_bytes()
    except FileNotFoundError:
        return HttpResponse("The source is unavailable. Contact the office administrator.", status=410)
    record(request.tenant, "document.downloaded", f"Downloaded {att.filename}", actor=request.user,
           object_type="attachment", object_ref=att.message.ref, details={"file": att.filename})
    resp = HttpResponse(data, content_type=att.content_type or "application/octet-stream")
    inline = request.GET.get("inline") == "1" and att.filename.lower().endswith(".pdf")
    resp["Content-Disposition"] = content_disposition_header(not inline, att.filename)
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@xframe_options_sameorigin
@tenant_view()
def taskpane(request, ref):
    """What the Outlook add-in shows beside a selected email (thin plugin, server-rendered)."""
    msg = get_object_or_404(IntakeMessage, tenant=request.tenant, ref=ref)
    ctx = {"selected": msg, **_pane_context(msg)}
    return render(request, "intake/taskpane.html", ctx)
