from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScoped
from apps.core.fields import EncryptedTextField
from apps.core.utils import unique_ref


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

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


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
