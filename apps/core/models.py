import hashlib
import secrets
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from .crypto import TenantCipher, generate_wrapped_key


class Tenant(models.Model):
    """A notary office. Every piece of customer data hangs off exactly one tenant."""

    class SecurityProfile(models.TextChoices):
        EU_SAAS = "eu_saas", "EU secure SaaS"
        HYBRID = "hybrid", "Hybrid secure agent"
        PRIVATE = "private", "Private / dedicated"

    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=160)
    slug = models.SlugField(unique=True)
    city = models.CharField(max_length=80, blank=True)
    security_profile = models.CharField(
        max_length=16, choices=SecurityProfile.choices, default=SecurityProfile.EU_SAAS
    )
    hosting_region = models.CharField(max_length=60, default="EU — Frankfurt (eu-central)")
    allow_external_ai = models.BooleanField(
        default=False,
        help_text="Permit document text to be sent to an external AI provider. Off means all extraction runs inside the tenant boundary.",
    )
    retention_days = models.PositiveIntegerField(default=180)
    data_key_wrapped = models.TextField(editable=False)
    api_token_prefix = models.CharField(max_length=12, blank=True, editable=False)
    api_token_hash = models.CharField(max_length=64, blank=True, editable=False)
    # Pilot baseline, captured before automation (slide 14 of the concept deck).
    baseline_minutes_per_matter = models.DecimalField(max_digits=6, decimal_places=1, default=Decimal("38.0"))
    baseline_rekey_error_pct = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal("4.5"))
    baseline_audit_completeness_pct = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal("55.0"))
    created_at = models.DateTimeField(default=timezone.now)
    audit_seq = models.PositiveIntegerField(default=0, editable=False)
    audit_head = models.CharField(max_length=64, default="0" * 64, editable=False)
    audit_seal = models.CharField(max_length=64, blank=True, editable=False)
    baseline_recorded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if getattr(settings, "LEXFLOW_DEPLOYMENT", "development") == "dedicated":
            from django.core.exceptions import ValidationError
            if self.slug != settings.LEXFLOW_TENANT_SLUG or Tenant.objects.exclude(pk=self.pk).exists():
                raise ValidationError("This dedicated deployment permits only its configured office.")
        if not self.data_key_wrapped:
            self.data_key_wrapped = generate_wrapped_key()
        if self.pk and kwargs.get("update_fields") is None:
            # A stale settings form must never overwrite the audit writer's head.
            kwargs["update_fields"] = [f.name for f in self._meta.concrete_fields
                                       if not f.primary_key and f.name not in ("audit_seq", "audit_head", "audit_seal")]
        super().save(*args, **kwargs)

    # --- encryption -------------------------------------------------------
    @property
    def cipher(self) -> TenantCipher:
        cached = getattr(self, "_cipher", None)
        if cached is None:
            cached = TenantCipher(self.data_key_wrapped)
            self._cipher = cached
        return cached

    @property
    def key_fingerprint(self) -> str:
        return self.cipher.fingerprint

    @property
    def vault_dir(self):
        return settings.LEXFLOW_VAULT_ROOT / "tenants" / str(self.uid)

    # --- API token for thin plugins ---------------------------------------
    def issue_api_token(self) -> str:
        raw = "lfx_" + secrets.token_urlsafe(32)
        self.api_token_prefix = raw[:10]
        self.api_token_hash = hashlib.sha256(raw.encode()).hexdigest()
        self.save(update_fields=["api_token_prefix", "api_token_hash"])
        return raw

    @classmethod
    def for_api_token(cls, raw: str):
        if not raw:
            return None
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return cls.objects.filter(api_token_hash=digest).first()


class Membership(models.Model):
    class Role(models.TextChoices):
        CLERK = "clerk", "Notary clerk"
        NOTARY = "notary", "Notary"
        ADMIN = "admin", "Office administrator"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CLERK)
    title = models.CharField(max_length=80, blank=True)

    class Meta:
        unique_together = [("user", "tenant")]

    def __str__(self):
        return f"{self.user} @ {self.tenant} ({self.role})"

    @property
    def can_approve(self):
        return self.role in (self.Role.NOTARY, self.Role.ADMIN)

    @property
    def can_configure(self):
        return self.role == self.Role.ADMIN


class TenantScoped(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="+")

    class Meta:
        abstract = True
