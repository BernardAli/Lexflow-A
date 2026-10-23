import hashlib
import hmac
from dataclasses import dataclass

from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.models import Tenant

from .models import GENESIS, AuditEvent


def digest(value) -> str:
    """Short, non-reversible fingerprint of a legal value for the audit trail."""
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:12]


def record(tenant, action, summary, *, actor=None, obj=None, object_type="", object_ref="", details=None):
    if obj is not None:
        object_type = object_type or obj.__class__.__name__.lower()
        object_ref = object_ref or getattr(obj, "ref", "") or str(obj.pk)
    if actor is not None and not getattr(actor, "is_authenticated", False):
        actor = None
    actor_label = (actor.get_full_name() or actor.get_username()) if actor else "LexFlow system"
    with transaction.atomic():
        # Serialise appends per tenant so the chain stays linear.
        state = Tenant.objects.select_for_update().get(pk=tenant.pk)
        last = AuditEvent.objects.filter(tenant=tenant).order_by("-seq").first()
        seq, head = (last.seq, last.hash) if last else (0, GENESIS)
        expected = state.cipher.sign_checkpoint(state.uid, state.audit_seq, state.audit_head)
        if seq != state.audit_seq or head != state.audit_head or (state.audit_seal and not hmac.compare_digest(expected, state.audit_seal)):
            raise ValidationError("The audit checkpoint does not match. Stop processing and investigate.")
        event = AuditEvent(
            tenant=tenant,
            seq=(last.seq + 1) if last else 1,
            created_at=timezone.now(),
            actor=actor,
            actor_label=actor_label,
            action=action,
            object_type=object_type,
            object_ref=object_ref,
            summary=summary[:255],
            details=details or {},
            prev_hash=last.hash if last else GENESIS,
        )
        event.hash = event.compute_hash()
        event.save()
        Tenant.objects.filter(pk=tenant.pk).update(
            audit_seq=event.seq, audit_head=event.hash,
            audit_seal=state.cipher.sign_checkpoint(state.uid, event.seq, event.hash),
        )
    return event


@dataclass
class ChainReport:
    ok: bool
    checked: int
    broken_at: int | None = None
    reason: str = ""
    head: str = ""


@transaction.atomic
def verify_chain(tenant) -> ChainReport:
    # A concurrent append must not look like tampering between the head read and
    # event iteration. Use the same office lock as every audit writer.
    state = Tenant.objects.select_for_update().get(pk=tenant.pk)
    prev = GENESIS
    count = 0
    for event in AuditEvent.objects.filter(tenant=tenant).select_related("tenant").order_by("seq").iterator():
        count += 1
        if event.seq != count:
            return ChainReport(False, count, event.seq, "Sequence gap — an event was removed.")
        if event.prev_hash != prev:
            return ChainReport(False, count, event.seq, "Link to the previous event does not match.")
        if event.compute_hash() != event.hash:
            return ChainReport(False, count, event.seq, "Event content was changed after it was written.")
        prev = event.hash
    expected = state.cipher.sign_checkpoint(state.uid, state.audit_seq, state.audit_head)
    if count != state.audit_seq or prev != state.audit_head:
        return ChainReport(False, count, count + 1, "Audit tail differs from its signed checkpoint.")
    if (count or state.audit_seal) and not hmac.compare_digest(expected, state.audit_seal):
        return ChainReport(False, count, count or 1, "Audit checkpoint signature is invalid.")
    return ChainReport(True, count, head=prev)
