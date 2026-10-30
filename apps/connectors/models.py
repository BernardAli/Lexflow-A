from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScoped
from apps.core.fields import EncryptedTextField
from apps.core.utils import unique_ref


class MappingProfileQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Mapping revisions are immutable. Create a new version.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Mapping revisions are immutable. Create a new version.")


class MappingProfile(TenantScoped):
    """Immutable revision. Approval also freezes the complete profile contents."""
    key = models.SlugField(max_length=60)
    version = models.PositiveIntegerField()
    name = models.CharField(max_length=120)
    matter_type = models.ForeignKey("studio.MatterType", on_delete=models.PROTECT)
    adapter_key = models.CharField(max_length=60, default="canonical-json-v1")
    target_version = models.CharField(max_length=80, blank=True)
    specification = models.CharField(max_length=255, blank=True)
    rules = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    objects = MappingProfileQuerySet.as_manager()

    class Meta:
        ordering = ["key", "-version"]
        constraints = [models.UniqueConstraint(fields=["tenant", "key", "version"], name="mapping_profile_revision")]

    def __str__(self):
        return f"{self.name} v{self.version}"

    def clean(self):
        from .adapters import get_adapter
        from .mapping import validate_sources
        if self.matter_type_id and self.matter_type.tenant_id != self.tenant_id:
            raise ValidationError("The workflow and profile must belong to the same office.")
        if not self.version or self.version < 1:
            raise ValidationError("Mapping versions start at 1.")
        get_adapter(self.adapter_key)
        if self.matter_type_id:
            validate_sources(self.rules, self.matter_type.fields.values_list("key", flat=True))

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Mapping revisions are immutable. Create a new version.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def snapshot(self):
        from .mapping import fingerprint
        self.clean()
        value = {"key": self.key, "version": self.version, "adapter": self.adapter_key,
                 "matter_type": self.matter_type.key, "target_version": self.target_version,
                 "specification": self.specification, "rules": self.rules}
        return {**value, "digest": fingerprint(value)}


class Connector(TenantScoped):
    class Delivery(models.TextChoices):
        DOWNLOAD = "download", "Download for manual import"
        FOLDER = "folder", "Approved import folder (receipt required)"
        WEBHOOK = "webhook", "Authenticated HTTP handoff"
    class Kind(models.TextChoices):
        XML = "xml", "Notary software import (XML)"
        JSON = "json", "Structured JSON"
        CSV = "csv", "Spreadsheet export (CSV)"

    class Tier(models.TextChoices):
        OFFICIAL_API = "official_api", "Official API"
        SDK = "sdk", "Official plugin / SDK"
        IMPORT = "import", "Import / export interface"

    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=6, choices=Kind.choices)
    tier = models.CharField(max_length=14, choices=Tier.choices, default=Tier.IMPORT)
    description = models.CharField(max_length=255, blank=True)
    destination = models.CharField(max_length=120, blank=True, help_text="Target system shown to reviewers.")
    signing_secret = EncryptedTextField(max_length=64, blank=True)
    delivery = models.CharField(max_length=10, choices=Delivery.choices, default=Delivery.DOWNLOAD)
    endpoint_key = models.SlugField(max_length=60, blank=True,
        help_text="Operator-configured endpoint key; URLs are never accepted from intake data.")
    is_active = models.BooleanField(default=True)
    mapping_profile = models.ForeignKey(MappingProfile, null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.mapping_profile_id:
            if self.mapping_profile.tenant_id != self.tenant_id:
                raise ValidationError("The mapping profile must belong to the connector's office.")
            if self.mapping_profile.adapter_key == "canonical-json-v1" and self.kind != self.Kind.JSON:
                raise ValidationError("The canonical case adapter produces JSON.")


class Transfer(TenantScoped):
    class Status(models.TextChoices):
        EXPORTED = "exported", "Export created — receipt required"
        PENDING = "pending", "Awaiting destination receipt"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"

    ref = models.CharField(max_length=20, unique=True, editable=False)
    matter = models.ForeignKey("matters.Matter", on_delete=models.CASCADE, related_name="transfers")
    connector = models.ForeignKey(Connector, on_delete=models.PROTECT, related_name="transfers")
    status = models.CharField(max_length=10, choices=Status.choices)
    payload_enc = models.BinaryField(default=b"")
    filename = models.CharField(max_length=120)
    content_type = models.CharField(max_length=60)
    checksum = models.CharField(max_length=64)
    signature = models.CharField(max_length=64, blank=True)
    field_count = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
    approval_hash = models.CharField(max_length=64, blank=True)
    idempotency_key = models.CharField(max_length=64, unique=True, null=True, editable=False)
    receipt_reference = EncryptedTextField(max_length=200, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    delivery_attempts = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    receipt_enc = models.BinaryField(default=b"", blank=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.ref:
            self.ref = unique_ref(Transfer, "TRF")
        super().save(*args, **kwargs)

    @property
    def payload(self) -> str:
        return self.tenant.cipher.decrypt_text(self.payload_enc)
