"""Approved exports with an explicit, audited destination-receipt step."""
import csv
import hashlib
import hmac
import io
import json
import os
import tempfile
import xml.etree.ElementTree as ET

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record, verify_chain
from apps.audit.models import AuditEvent
from apps.core.models import Membership, Tenant
from apps.matters.models import Matter
from apps.studio.validation import validate_paths

from .models import Connector, Transfer
from . import protocol


def make_approval_snapshot(matter):
    from apps.matters.case_data import canonical_case, has_details
    from .profiles import freeze_profiles
    snapshot = {
        "version": 2, "ref": matter.ref, "matter_type": matter.matter_type.key,
        "data_class": "synthetic" if matter.is_demo else "office",
        "received_at": matter.message.received_at.isoformat(),
        "approved_at": matter.approved_at.isoformat() if matter.approved_at else None,
        "approved_by": (matter.approved_by.get_full_name() or matter.approved_by.get_username()) if matter.approved_by else None,
        "approved_by_id": matter.approved_by_id,
        "fields": [{"key": f.definition.key, "path": f.definition.destination_path,
                    "label": f.definition.label, "value": f.normalized or f.value,
                    "status": f.status, "source_sha256": f.source.sha256 if f.source else None}
                   for f in matter.fields.select_related("definition", "source").order_by("definition__sort_order", "id")],
    }
    snapshot["case"] = canonical_case(matter, {f["key"]: f["value"] for f in snapshot["fields"]})
    snapshot["has_case_details"] = has_details(matter)
    snapshot["mapping_profiles"] = freeze_profiles(matter)
    return snapshot


def approval_snapshot(matter):
    if not matter.approval_enc or not matter.approval_hash:
        raise ValidationError("This approval predates frozen exports. Reopen, review and approve it again.")
    encoded = matter.tenant.cipher.decrypt_text(matter.approval_enc)
    if not hmac.compare_digest(hashlib.sha256(encoded.encode()).hexdigest(), matter.approval_hash):
        raise ValidationError("The approved snapshot failed its integrity check.")
    snapshot = json.loads(encoded)
    event = AuditEvent.objects.filter(tenant=matter.tenant, object_ref=matter.ref, action="matter.approved").first()
    if snapshot.get("ref") != matter.ref or not event or event.details.get("approval") != matter.approval_hash:
        raise ValidationError("The frozen export is not bound to this matter's audited approval.")
    return snapshot


def _snapshot(matter):
    return approval_snapshot(matter) if matter.is_locked else make_approval_snapshot(matter)


def build_payload(matter):
    return _build(_snapshot(matter))


def _build(snapshot):
    validate_paths(f["path"] for f in snapshot["fields"])
    tree, flat = {}, []
    for field in snapshot["fields"]:
        if not field["value"]:
            continue
        path, value, label = field["path"], field["value"], field["label"]
        flat.append((path, value, label))
        node = tree
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return tree, flat


def _xml(snapshot, tree):
    root = ET.Element("LexFlowExport", {"version": "1.0", "generator": "LexFlow AI"})
    head = ET.SubElement(root, "Vorgang", {"ref": snapshot["ref"], "typ": snapshot["matter_type"]})
    ET.SubElement(head, "Eingang").text = snapshot["received_at"]
    ET.SubElement(head, "Freigabe", {"durch": snapshot["approved_by"] or ""}).text = snapshot["approved_at"] or ""
    data = ET.SubElement(root, "Daten")

    def add(parent, obj):
        for key, value in obj.items():
            el = ET.SubElement(parent, key)
            if isinstance(value, dict):
                add(el, value)
            else:
                el.text = str(value)
    add(data, tree)
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def render(connector, matter):
    from .adapters import get_adapter
    from .profiles import profile_for_snapshot
    if connector.tenant_id != matter.tenant_id or not connector.is_active:
        raise ValidationError("That connector is not available for this office.")
    snapshot = _snapshot(matter)
    profile = profile_for_snapshot(connector, snapshot)
    if profile:
        return get_adapter(profile["adapter"]).render(snapshot, profile)
    if snapshot.get("has_case_details"):
        raise ValidationError("This case includes additional details. Select a structured case export to include them.")
    tree, flat = _build(snapshot)
    stem = f'{snapshot["ref"]}-{snapshot["matter_type"]}'
    if connector.kind == Connector.Kind.XML:
        return _xml(snapshot, tree), f"{stem}.xml", "application/xml", len(flat)
    if connector.kind == Connector.Kind.JSON:
        body = {k: snapshot[k] for k in ("ref", "matter_type", "approved_at", "approved_by")}
        body["schema"] = "lexflow.handoff/1"
        body["data_class"] = snapshot.get("data_class", "office")
        body["data"] = tree
        return json.dumps(body, indent=2, ensure_ascii=False) + "\n", f"{stem}.json", "application/json", len(flat)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["ref", "destination_path", "label", "value"])
    for path, value, label in flat:
        # Prevent spreadsheet formula execution in a manually opened CSV.
        safe = "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
        writer.writerow([snapshot["ref"], path, label, safe])
    return buf.getvalue(), f"{stem}.csv", "text/csv", len(flat)


def export_path(transfer):
    directory = settings.LEXFLOW_EXPORT_ROOT / str(transfer.tenant.uid) / str(transfer.connector_id)
    return directory / f"{transfer.ref}{os.path.splitext(transfer.filename)[1]}"


def _write_export(transfer):
    path = export_path(transfer)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = transfer.payload.encode()
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != transfer.checksum:
            raise OSError("Existing export differs from approved bytes.")
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".lexflow-", delete=False) as handle:
            temporary = handle.name
            os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _approver(tenant, user):
    if not Membership.objects.filter(tenant=tenant, user=user, role__in=["notary", "admin"]).exists():
        raise PermissionDenied("Only an office approver can export or confirm delivery.")


def checked_payload(exported, *, allow_legacy=False):
    body = exported.payload
    if not hmac.compare_digest(hashlib.sha256(body.encode()).hexdigest(), exported.checksum):
        raise ValidationError("The stored export failed its checksum check.")
    if not exported.approval_hash:
        if allow_legacy:
            return body
        raise ValidationError("This earlier export requires a new frozen approval.")
    events = AuditEvent.objects.filter(tenant=exported.tenant, object_ref=exported.matter.ref, action="transfer.exported")
    bound = any(event.details.get("transfer") == exported.ref and
                event.details.get("sha256") == exported.checksum and
                event.details.get("approval") == exported.approval_hash for event in events)
    if not bound or not verify_chain(exported.tenant).ok:
        raise ValidationError("The export bytes do not match their audited approval record.")
    return body


@transaction.atomic
def transfer(matter, connector, user):
    Tenant.objects.select_for_update().get(pk=matter.tenant_id)
    matter = Matter.objects.select_for_update().get(pk=matter.pk)
    _approver(matter.tenant, user)
    connector = Connector.objects.get(pk=connector.pk)
    if connector.tenant_id != matter.tenant_id or not connector.is_active:
        raise ValidationError("That connector is not available for this office.")
    if not matter.is_locked:
        raise ValidationError("Approve the matter before creating an export.")
    if not verify_chain(matter.tenant).ok:
        raise ValidationError("The audit trail must verify before exporting.")
    approval_snapshot(matter)
    key = hashlib.sha256(f"{matter.tenant.uid}|{matter.ref}|{connector.pk}|{matter.approval_hash}".encode()).hexdigest()
    existing = Transfer.objects.filter(idempotency_key=key).first()
    if existing and existing.status != Transfer.Status.FAILED:
        checked_payload(existing)
        return existing
    body, filename, content_type, count = render(connector, matter)
    checksum = hashlib.sha256(body.encode()).hexdigest()
    signature = hmac.new(connector.signing_secret.encode(), body.encode(), hashlib.sha256).hexdigest() if (
        connector.kind == Connector.Kind.JSON and connector.signing_secret) else ""
    result = existing or Transfer(tenant=matter.tenant, matter=matter, connector=connector, created_by=user,
                                  idempotency_key=key, approval_hash=matter.approval_hash)
    result.status = Transfer.Status.EXPORTED
    result.payload_enc = matter.tenant.cipher.encrypt_text(body)
    result.filename, result.content_type, result.field_count = filename, content_type, count
    result.checksum, result.signature, result.error = checksum, signature, ""
    result.save()
    record(matter.tenant, "transfer.exported", f"Created an approved export with {count} fields; receipt required",
           actor=user, obj=matter, details={"transfer": result.ref, "sha256": checksum, "approval": matter.approval_hash,
                                          "mapping": approval_snapshot(matter).get("mapping_profiles", {}).get(str(connector.pk), {}).get("digest", "")})
    if connector.delivery == Connector.Delivery.FOLDER:
        try:
            _write_export(result)
            result.status = Transfer.Status.PENDING
        except OSError:
            result.status = Transfer.Status.FAILED
            result.error = "Could not write the approved import folder. Check storage and retry."
            record(matter.tenant, "transfer.failed", "Export folder write failed", actor=user, obj=matter,
                   details={"transfer": result.ref})
        result.save(update_fields=["status", "error"])
    return result


@transaction.atomic
def acknowledge(transfer, user, *, checksum, receipt_reference):
    Tenant.objects.select_for_update().get(pk=transfer.tenant_id)
    matter = Matter.objects.select_for_update().get(pk=transfer.matter_id)
    result = Transfer.objects.select_for_update().get(pk=transfer.pk)
    _approver(matter.tenant, user)
    if not matter.approval_hash or not result.approval_hash or result.approval_hash != matter.approval_hash or not matter.is_locked:
        raise ValidationError("This export belongs to a superseded approval.")
    approval_snapshot(matter)
    if not verify_chain(matter.tenant).ok:
        raise ValidationError("The audit trail must verify before confirming receipt.")
    if not checksum or not hmac.compare_digest(result.checksum, checksum.strip()):
        raise ValidationError("The received payload checksum does not match the approved export.")
    checked_payload(result)
    reference = (receipt_reference or "").strip()
    if not reference or len(reference) > 200:
        raise ValidationError("Enter the destination's receipt or case reference (1–200 characters).")
    if result.connector.delivery == Connector.Delivery.WEBHOOK:
        encoded = result.tenant.cipher.decrypt(result.receipt_enc) if result.receipt_enc else b""
        bound = any(event.details.get("transfer") == result.ref and
                    event.details.get("receipt_sha256") == hashlib.sha256(encoded).hexdigest()
                    for event in AuditEvent.objects.filter(tenant=result.tenant, object_ref=matter.ref,
                                                          action="transfer.receipt_verified"))
        if not encoded or not bound:
            raise ValidationError("An HTTP handoff requires an authenticated destination receipt; use Send or Retry.")
        try:
            envelope = json.loads(encoded)
            receipt = envelope["receipt"]
            valid = hmac.compare_digest(protocol.receipt_signature(result.connector.signing_secret, receipt),
                                        envelope["signature"])
            valid &= receipt.get("sha256") == checksum and receipt.get("approval_hash") == result.approval_hash
            valid &= receipt.get("reference") == reference and receipt.get("idempotency_key") == result.idempotency_key
            valid &= receipt.get("transfer") == result.ref and receipt.get("status") == "accepted"
        except (ValueError, KeyError, TypeError):
            valid = False
        if not valid:
            raise ValidationError("The stored HTTP receipt does not verify for this transfer.")
    if result.status == Transfer.Status.DELIVERED:
        return result
    if result.status not in (Transfer.Status.EXPORTED, Transfer.Status.PENDING):
        raise ValidationError("Only an exported payload can be acknowledged.")
    result.status, result.receipt_reference = Transfer.Status.DELIVERED, reference
    result.acknowledged_at = timezone.now()
    result.save(update_fields=["status", "receipt_reference", "acknowledged_at"])
    matter.status, matter.transferred_at = Matter.Status.TRANSFERRED, result.acknowledged_at
    matter.save(update_fields=["status", "transferred_at"])
    record(matter.tenant, "matter.transferred", "Destination receipt confirmed by an approver", actor=user, obj=matter,
           details={"transfer": result.ref, "sha256": checksum, "receipt_digest": hashlib.sha256(reference.encode()).hexdigest()})
    return result


def receive_demo(transfer, user):
    """An explicitly synthetic destination, reading the actual exported file."""
    if not settings.LEXFLOW_DEMO_MODE or not transfer.matter.is_demo:
        raise ValidationError("The demonstration receiver accepts synthetic demo matters only.")
    _approver(transfer.tenant, user)
    path = export_path(transfer)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ValidationError("Create an export with the demo import-folder connector first.") from exc
    checksum = hashlib.sha256(data).hexdigest()
    if checksum != transfer.checksum:
        raise ValidationError("The demo receiver rejected a modified export.")
    if transfer.content_type == "application/xml":
        root = ET.fromstring(data)
        if root.tag != "LexFlowExport" or root.find("Daten") is None:
            raise ValidationError("The demo receiver rejected the XML schema.")
    else:
        raise ValidationError("The demo receiver currently accepts the pilot XML schema only.")
    result = acknowledge(transfer, user, checksum=checksum, receipt_reference="DEMO-RECEIPT-" + transfer.ref)
    return result


@transaction.atomic
def deliver_webhook(exported, user):
    """Serialize approval/reopen against a bounded send; retry ambiguous outcomes safely."""
    Tenant.objects.select_for_update().get(pk=exported.tenant_id)
    matter = Matter.objects.select_for_update().get(pk=exported.matter_id)
    result = Transfer.objects.select_for_update().select_related("connector", "tenant").get(pk=exported.pk)
    _approver(matter.tenant, user)
    connector = result.connector
    if connector.tenant_id != matter.tenant_id or not connector.is_active or connector.delivery != "webhook":
        raise ValidationError("This transfer does not have an active HTTP connector for the office.")
    if not matter.is_locked or result.approval_hash != matter.approval_hash:
        raise ValidationError("This transfer belongs to a superseded approval.")
    approval_snapshot(matter)
    body = checked_payload(result)
    if result.status == Transfer.Status.DELIVERED:
        return result
    protocol.endpoint(connector)
    result.delivery_attempts += 1
    result.last_attempt_at = timezone.now()
    result.status, result.error = Transfer.Status.PENDING, ""
    result.save(update_fields=["delivery_attempts", "last_attempt_at", "status", "error"])
    record(matter.tenant, "transfer.attempted", "Attempted authenticated HTTP handoff", actor=user,
           obj=matter, details={"transfer": result.ref, "attempt": result.delivery_attempts,
                                "endpoint_key": connector.endpoint_key})
    try:
        receipt, encoded = protocol.send(connector, result, body)
    except ValidationError as exc:
        # Pending covers timeouts after remote acceptance: never invent a failure
        # or a delivery. An explicit retry gets the receiver's original receipt.
        result.error = " ".join(exc.messages)[:255]
        result.save(update_fields=["error"])
        record(matter.tenant, "transfer.unconfirmed", "HTTP handoff remains unconfirmed; safe to retry", actor=user,
               obj=matter, details={"transfer": result.ref, "attempt": result.delivery_attempts})
        return result
    result.receipt_enc = matter.tenant.cipher.encrypt(encoded)
    result.save(update_fields=["receipt_enc"])
    record(matter.tenant, "transfer.receipt_verified", "Authenticated destination receipt verified", actor=user,
           obj=matter, details={"transfer": result.ref, "receipt_sha256": hashlib.sha256(encoded).hexdigest()})
    return acknowledge(result, user, checksum=receipt["sha256"], receipt_reference=receipt["reference"])
