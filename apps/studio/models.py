from decimal import Decimal

from django.db import models

from apps.core.models import TenantScoped


class MatterType(TenantScoped):
    """A notarial workflow the office has taught LexFlow (e.g. property purchase)."""

    key = models.SlugField(max_length=40)
    name = models.CharField(max_length=80)
    german_name = models.CharField(max_length=80, blank=True)
    description = models.CharField(max_length=255, blank=True)
    subject_keywords = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        unique_together = [("tenant", "key")]

    def __str__(self):
        return self.name


class DocumentType(TenantScoped):
    key = models.SlugField(max_length=40)
    name = models.CharField(max_length=80)
    keywords = models.JSONField(default=list, help_text="Phrases that identify this document.")
    matter_type = models.ForeignKey(
        MatterType, null=True, blank=True, on_delete=models.SET_NULL, related_name="document_types"
    )
    weight = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("1.00"))

    class Meta:
        ordering = ["name"]
        unique_together = [("tenant", "key")]

    def __str__(self):
        return self.name


class FieldDefinition(models.Model):
    class DataType(models.TextChoices):
        NAME = "name", "Person or company name"
        ADDRESS = "address", "Postal address"
        MONEY = "money", "Amount in euro"
        DATE = "date", "Date"
        GRUNDBUCH = "grundbuch", "Land register sheet"
        PARCEL = "parcel", "Parcel number"
        TEXT = "text", "Free text"

    matter_type = models.ForeignKey(MatterType, on_delete=models.CASCADE, related_name="fields")
    key = models.SlugField(max_length=40)
    label = models.CharField(max_length=80)
    data_type = models.CharField(max_length=12, choices=DataType.choices, default=DataType.TEXT)
    destination_path = models.CharField(
        max_length=120, help_text="Where the approved value is written, e.g. Beteiligter.Kaeufer.Name"
    )
    anchors = models.JSONField(default=list, help_text="Labels that precede this value in documents.")
    required = models.BooleanField(default=True)
    always_review = models.BooleanField(
        default=False, help_text="Never prefill silently — a person must confirm this field every time."
    )
    auto_threshold = models.DecimalField(
        max_digits=3, decimal_places=2, default=Decimal("0.90"),
        help_text="Minimum confidence to prefill without review.",
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        unique_together = [("matter_type", "key")]

    def __str__(self):
        return f"{self.matter_type.name}: {self.label}"

    @property
    def tenant(self):
        return self.matter_type.tenant
