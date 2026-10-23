"""Export/verify an independent audit checkpoint held by the office operator."""
import hmac
import json
import os
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction
from apps.core.models import Tenant

from .models import AuditEvent
from .services import verify_chain


@transaction.atomic
def export_checkpoint(office, path):
    office = Tenant.objects.select_for_update().get(pk=office.pk)
    if not verify_chain(office).ok:
        raise ValidationError("The audit chain must verify before creating an external checkpoint.")
    office.refresh_from_db()
    checkpoint = {"version": 1, "office": str(office.uid), "seq": office.audit_seq, "head": office.audit_head,
                  "seal": office.cipher.sign_checkpoint(office.uid, office.audit_seq, office.audit_head)}
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValidationError("Checkpoints are never overwritten. Choose a new filename.") from exc
    with os.fdopen(fd, "w") as handle:
        json.dump(checkpoint, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    return checkpoint


@transaction.atomic
def verify_checkpoint(office, path):
    office = Tenant.objects.select_for_update().get(pk=office.pk)
    try:
        checkpoint = json.loads(Path(path).read_text())
        seq, head, seal = checkpoint["seq"], checkpoint["head"], checkpoint["seal"]
        if (checkpoint["version"] != 1 or checkpoint["office"] != str(office.uid) or type(seq) is not int or
                seq < 0 or not isinstance(head, str) or len(head) != 64 or not isinstance(seal, str) or
                not hmac.compare_digest(office.cipher.sign_checkpoint(office.uid, seq, head), seal)):
            raise ValueError
        if not verify_chain(office).ok:
            raise ValueError
        office.refresh_from_db()
        event = AuditEvent.objects.filter(tenant=office, seq=seq).first() if seq else None
        expected = event.hash if event else "0" * 64 if not seq else None
        if office.audit_seq < seq or expected != head:
            raise ValidationError("The database is older than or diverges from the trusted external audit checkpoint.")
        return {"verified": True, "checkpoint_seq": seq, "current_seq": office.audit_seq}
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValidationError("External checkpoint authentication or audit verification failed.") from exc
