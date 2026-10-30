"""Thin-plugin API (Outlook add-in, scanner agent, Word plugin).

Authentication: `Authorization: Bearer <office API token>`. The token
identifies the office; there is no way to address another office's data.
"""
from django.http import JsonResponse
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.core.models import Tenant
from apps.matters.models import Matter
from apps.matters.services import approval_blockers, ingest_message

from .models import IntakeMessage
from .validation import read_uploads


def _tenant(request):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    tenant = Tenant.for_api_token(header[7:].strip())
    if tenant and settings.LEXFLOW_DEPLOYMENT == "dedicated" and tenant.slug != settings.LEXFLOW_TENANT_SLUG:
        return None
    return tenant


def _denied():
    return JsonResponse({"error": "invalid_token", "detail": "Send a valid office API token."}, status=401)


def matter_json(matter):
    blockers = approval_blockers(matter)
    if matter.is_locked:
        from apps.connectors.services import approval_snapshot
        try:
            approval_snapshot(matter)
        except ValidationError as exc:
            blockers = exc.messages
        else:
            blockers = []
    return {
        "ref": matter.ref,
        "status": matter.status,
        "status_label": matter.get_status_display(),
        "matter_type": matter.matter_type.key if matter.matter_type else None,
        "type_confidence": float(matter.type_confidence),
        "blocked": bool(blockers),
        "blockers": blockers,
        "can_export": matter.is_locked and not blockers,
        "ready_for_approval": not matter.is_locked and not blockers,
        "has_conflicts": matter.open_conflicts.exists(),
        "approved": matter.is_locked,
        "approval_hash": matter.approval_hash or None,
        "fields": [
            {"key": f.definition.key, "label": f.definition.label, "value": f.display_value,
             "confidence": float(f.confidence), "status": f.status, "destination": f.definition.destination_path}
            for f in matter.fields.select_related("definition")
        ],
    }


@csrf_exempt
@require_POST
def intake(request):
    tenant = _tenant(request)
    if tenant is None:
        return _denied()
    try:
        files = read_uploads(request.FILES.getlist("files"))
    except ValidationError as exc:
        return JsonResponse({"error": "invalid_upload", "detail": exc.messages}, status=400)
    subject = request.POST.get("subject", "").strip()
    if not subject:
        return JsonResponse({"error": "subject_required"}, status=400)
    try:
        msg = ingest_message(
            tenant, subject=subject, body=request.POST.get("body", ""), files=files, source=IntakeMessage.Source.API,
            sender_name=request.POST.get("sender_name", ""), sender_email=request.POST.get("sender_email", ""),
            external_id=request.POST.get("external_id", "")[:120], is_demo=settings.LEXFLOW_DEMO_MODE,
        )
    except ValidationError as exc:
        return JsonResponse({"error": "invalid_intake", "detail": exc.messages}, status=400)
    if msg.status == IntakeMessage.Status.FAILED:
        return JsonResponse({"message": msg.ref, "status": msg.status, "error": msg.error}, status=202)
    return JsonResponse({"message": msg.ref, "matter": matter_json(msg.matter)}, status=201)


@require_GET
def matter_detail(request, ref):
    tenant = _tenant(request)
    if tenant is None:
        return _denied()
    matter = Matter.objects.filter(tenant=tenant, ref=ref).first()
    if matter is None:
        return JsonResponse({"error": "not_found"}, status=404)
    return JsonResponse(matter_json(matter))


@require_GET
@transaction.atomic
def approved_case(request, ref):
    """Approved data for adapters; never reconstruct an approval from live fields."""
    from apps.audit.services import verify_chain
    from apps.connectors.services import approval_snapshot
    tenant = _tenant(request)
    if tenant is None:
        return _denied()
    Tenant.objects.select_for_update().get(pk=tenant.pk)
    matter = Matter.objects.filter(tenant=tenant, ref=ref).first()
    if matter is None:
        return JsonResponse({"error": "not_found"}, status=404)
    try:
        if not matter.is_locked or not verify_chain(tenant).ok:
            raise ValidationError("A verified, current approval is required.")
        snapshot = approval_snapshot(matter)
        if "case" not in snapshot:
            raise ValidationError("Reopen and approve this case to include structured details.")
    except ValidationError as exc:
        return JsonResponse({"error": "approval_required", "detail": exc.messages}, status=409)
    response = JsonResponse({"approval_hash": matter.approval_hash, "approved_at": snapshot["approved_at"],
                            "case": snapshot["case"]})
    response["Cache-Control"] = "no-store"
    return response
