import hashlib
import uuid

from django.db import models
from django.utils import timezone

from apps.core.models import TenantScoped
from apps.core.fields import EncryptedTextField
from apps.core.utils import unique_ref


class IntakeMessage(TenantScoped):
    class Source(models.TextChoices):
        EMAIL = "email", "Outlook / email"
        UPLOAD = "upload", "Manual upload"
        FOLDER = "folder", "Scanner / folder agent"
        API = "api", "Plugin API"

    class Status(models.TextChoices):
        NEW = "new", "Waiting"
        PROCESSED = "processed", "Processed"
        FAILED = "failed", "Failed"

    ref = models.CharField(max_length=20, unique=True, editable=False)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.EMAIL)
    sender_name = EncryptedTextField(max_length=120, blank=True)
    sender_email = EncryptedTextField(max_length=254, blank=True)
    subject = EncryptedTextField(max_length=255)
    is_demo = models.BooleanField(default=False)
    body_enc = models.BinaryField(blank=True, default=b"")
    received_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW)
    external_id = models.CharField(max_length=120, blank=True, db_index=True)
    error = models.CharField(max_length=255, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]
        constraints = [models.UniqueConstraint(fields=["tenant", "external_id"], condition=~models.Q(external_id=""), name="intake_external_id_per_office")]

    def __str__(self):
        return f"{self.ref} {self.subject}"

    def save(self, *args, **kwargs):
        if not self.ref:
            self.ref = unique_ref(IntakeMessage, "INT")
        super().save(*args, **kwargs)

    @property
    def body(self) -> str:
        return self.tenant.cipher.decrypt_text(self.body_enc)

    @body.setter
    def body(self, value: str):
        self.body_enc = self.tenant.cipher.encrypt_text(value)

    @property
    def files(self):
        return self.attachments.filter(kind=Attachment.Kind.FILE)


class Attachment(TenantScoped):
    class Kind(models.TextChoices):
        FILE = "file", "File"
        BODY = "body", "Email text"

    message = models.ForeignKey(IntakeMessage, on_delete=models.CASCADE, related_name="attachments")
    kind = models.CharField(max_length=6, choices=Kind.choices, default=Kind.FILE)
    filename = EncryptedTextField(max_length=200)
    content_type = models.CharField(max_length=100, blank=True)
    size = models.PositiveIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    blob_name = models.CharField(max_length=80, blank=True)
    text_enc = models.BinaryField(blank=True, default=b"")
    text_method = models.CharField(max_length=20, blank=True)
    page_count = models.PositiveSmallIntegerField(default=0)
    doc_type = models.ForeignKey(
        "studio.DocumentType", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    doc_type_confidence = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["kind", "id"]

    def __str__(self):
        return self.filename

    # --- encrypted storage -------------------------------------------------
    def store_bytes(self, data: bytes):
        self.size = len(data)
        self.sha256 = hashlib.sha256(data).hexdigest()
        self.blob_name = f"{uuid.uuid4().hex}.lfx"
        directory = self.tenant.vault_dir
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (directory / self.blob_name).open("xb") as handle:
            import os
            os.fchmod(handle.fileno(), 0o600)
            handle.write(self.tenant.cipher.encrypt(data))

    def read_bytes(self) -> bytes:
        if not self.blob_name:
            return b""
        return self.tenant.cipher.decrypt((self.tenant.vault_dir / self.blob_name).read_bytes())

    @property
    def text(self) -> str:
        cached = getattr(self, "_text_cache", None)
        if cached is None:
            cached = self.tenant.cipher.decrypt_text(self.text_enc)
            self._text_cache = cached
        return cached

    @text.setter
    def text(self, value: str):
        self._text_cache = value
        self.text_enc = self.tenant.cipher.encrypt_text(value)

    @property
    def display_name(self):
        return "Email text" if self.kind == self.Kind.BODY else self.filename


class PendingBlobDeletion(TenantScoped):
    blob_name = models.CharField(max_length=80)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "blob_name"], name="pending_blob_per_office")]
