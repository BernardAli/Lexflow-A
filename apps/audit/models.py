import hashlib
import json

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import Tenant
from apps.core.fields import EncryptedJSONField, EncryptedTextField

GENESIS = "0" * 64


class AuditImmutable(Exception):
    pass


class AuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AuditImmutable("Audit events cannot be modified.")

    def delete(self):
        raise AuditImmutable("Audit events cannot be deleted.")

    def bulk_update(self, *args, **kwargs):
        raise AuditImmutable("Audit events cannot be modified.")


class AuditEvent(models.Model):
    """Append-only, hash-chained event log — one chain per tenant.

    Each event's hash covers its own content plus the previous event's hash, so
    editing or deleting any past row breaks verification from that point on.
    Values of legal data are never written here: only references and digests.
    """

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="audit_events")
    seq = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    objects = AuditQuerySet.as_manager()
    actor_label = EncryptedTextField(max_length=120)
    action = models.CharField(max_length=60, db_index=True)
    object_type = models.CharField(max_length=40, blank=True)
    object_ref = models.CharField(max_length=40, blank=True, db_index=True)
    summary = EncryptedTextField(max_length=255)
    details = EncryptedJSONField(default=dict, blank=True)
    prev_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64, unique=True)

    class Meta:
        ordering = ["-seq"]
        unique_together = [("tenant", "seq")]

    def __str__(self):
        return f"#{self.seq} {self.action} {self.object_ref}"

    def canonical(self) -> str:
        return json.dumps(
            {
                "tenant": str(self.tenant.uid),
                "seq": self.seq,
                "at": self.created_at.isoformat(),
                "actor": self.actor_label,
                "action": self.action,
                "object_type": self.object_type,
                "object_ref": self.object_ref,
                "summary": self.summary,
                "details": self.details,
                "prev": self.prev_hash,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def compute_hash(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise AuditImmutable("Audit events cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditImmutable("Audit events cannot be deleted.")

    @property
    def is_system(self):
        return self.actor_id is None
