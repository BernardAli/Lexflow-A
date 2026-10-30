from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScoped
from apps.core.fields import EncryptedJSONField, EncryptedTextField
from apps.core.utils import format_eur, pct, unique_ref


class Matter(TenantScoped):
    class Status(models.TextChoices):
        UNCLASSIFIED = "unclassified", "Needs classification"
        NEEDS_REVIEW = "needs_review", "Needs review"
        READY = "ready", "Ready for approval"
        APPROVED = "approved", "Approved"
        TRANSFERRED = "transferred", "Transferred"

    ref = models.CharField(max_length=20, unique=True, editable=False)
    message = models.OneToOneField("intake.IntakeMessage", on_delete=models.CASCADE, related_name="matter")
    matter_type = models.ForeignKey("studio.MatterType", null=True, blank=True, on_delete=models.PROTECT)
    type_confidence = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    title = EncryptedTextField(max_length=200)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.NEEDS_REVIEW)
    provider = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    transferred_at = models.DateTimeField(null=True, blank=True)
    approval_enc = models.BinaryField(default=b"", editable=False)
    approval_hash = models.CharField(max_length=64, blank=True, editable=False)
    is_demo = models.BooleanField(default=False)
    initial_metrics = models.JSONField(default=dict, editable=False)
    corrected_keys = models.JSONField(default=list, editable=False)
    case_data = EncryptedJSONField(default=dict, blank=True, editable=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.ref} {self.title}"

    def save(self, *args, **kwargs):
        if not self.ref:
            self.ref = unique_ref(Matter, "MAT")
        super().save(*args, **kwargs)

    @property
    def is_locked(self):
        return self.status in (self.Status.APPROVED, self.Status.TRANSFERRED)

    @property
    def type_pct(self):
        return pct(self.type_confidence)

    @property
    def open_conflicts(self):
        return self.fields.filter(status=ExtractedField.Status.CONFLICT)

    @property
    def stage_index(self):
        """Position on the 7-stage pipeline: receive → audit."""
        return {
            self.Status.UNCLASSIFIED: 2,
            self.Status.NEEDS_REVIEW: 5,
            self.Status.READY: 5,
            self.Status.APPROVED: 6,
            self.Status.TRANSFERRED: 7,
        }[self.status]


class ExtractedField(models.Model):
    class Status(models.TextChoices):
        AUTO = "auto", "Prefilled"
        REVIEW = "review", "Check needed"
        CONFLICT = "conflict", "Conflict"
        MISSING = "missing", "Missing"
        CONFIRMED = "confirmed", "Confirmed"
        CORRECTED = "corrected", "Corrected"

    OPEN = (Status.REVIEW, Status.CONFLICT, Status.MISSING)

    matter = models.ForeignKey(Matter, on_delete=models.CASCADE, related_name="fields")
    definition = models.ForeignKey("studio.FieldDefinition", on_delete=models.PROTECT, related_name="+")
    value = EncryptedTextField(max_length=500, blank=True)
    normalized = EncryptedTextField(max_length=500, blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    status = models.CharField(max_length=10, choices=Status.choices)
    initial_status = models.CharField(max_length=10, choices=Status.choices)
    source = models.ForeignKey("intake.Attachment", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    span_start = models.IntegerField(null=True, blank=True)
    span_end = models.IntegerField(null=True, blank=True)
    method = models.CharField(max_length=30, blank=True)
    note = EncryptedTextField(max_length=255, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["definition__sort_order", "definition_id"]
        unique_together = [("matter", "definition")]

    def __str__(self):
        return f"{self.matter.ref} {self.definition.key}={self.value}"

    @property
    def confidence_pct(self):
        return pct(self.confidence)

    @property
    def is_open(self):
        return self.status in self.OPEN

    @property
    def display_value(self):
        if not self.normalized:
            return self.value
        dt = self.definition.data_type
        if dt == "money":
            return format_eur(self.normalized)
        if dt == "date":
            y, m, d = self.normalized.split("-")
            return f"{d}.{m}.{y}"
        return self.normalized

    @property
    def tone(self):
        if self.status in (self.Status.CONFLICT, self.Status.MISSING):
            return "seal"
        if self.status == self.Status.REVIEW:
            return "amber"
        if self.status in (self.Status.CONFIRMED, self.Status.CORRECTED):
            return "ink"
        return "moss"


class FieldCandidate(models.Model):
    """Every value found for a field in every document — the evidence behind a field."""

    field = models.ForeignKey(ExtractedField, on_delete=models.CASCADE, related_name="candidates")
    value = EncryptedTextField(max_length=500)
    normalized = EncryptedTextField(max_length=500, blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=4)
    source = models.ForeignKey("intake.Attachment", on_delete=models.CASCADE, related_name="+")
    span_start = models.IntegerField()
    span_end = models.IntegerField()
    method = models.CharField(max_length=30)
    anchor = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["-confidence"]

    @property
    def confidence_pct(self):
        return pct(self.confidence)

    @property
    def display_value(self):
        if self.field.definition.data_type == "money" and self.normalized:
            return format_eur(self.normalized)
        if self.field.definition.data_type == "date" and self.normalized:
            y, m, d = self.normalized.split("-")
            return f"{d}.{m}.{y}"
        return self.normalized or self.value


class FieldRevision(models.Model):
    field = models.ForeignKey(ExtractedField, on_delete=models.CASCADE, related_name="revisions")
    old_value = EncryptedTextField(max_length=500, blank=True)
    new_value = EncryptedTextField(max_length=500, blank=True)
    action = models.CharField(max_length=20)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-at"]


ZERO = Decimal("0")


class ExtractionSnapshot(models.Model):
    matter = models.ForeignKey(Matter, on_delete=models.CASCADE, related_name="extraction_history")
    payload_enc = models.BinaryField()
    created_at = models.DateTimeField(default=timezone.now)
