"""Redact expired source contents and retry reference-safe blob deletion."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.core.models import Tenant
from apps.intake.models import Attachment, IntakeMessage, PendingBlobDeletion
from apps.matters.models import Matter


class Command(BaseCommand):
    help = "Purge expired acknowledged source contents; preserve active references."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        for tenant in Tenant.objects.all():
            cutoff = timezone.now() - timedelta(days=tenant.retention_days)
            messages = IntakeMessage.objects.filter(tenant=tenant, matter__status=Matter.Status.TRANSFERRED,
                                                     matter__transferred_at__lt=cutoff)
            atts = Attachment.objects.filter(message__in=messages).exclude(text_method="purged")
            count = atts.count()
            if opts["dry_run"]:
                self.stdout.write(f"{tenant.slug}: {count} document(s) eligible")
                continue
            with transaction.atomic():
                Tenant.objects.select_for_update().get(pk=tenant.pk)
                for att in atts:
                    if att.blob_name:
                        PendingBlobDeletion.objects.get_or_create(tenant=tenant, blob_name=att.blob_name)
                    att.blob_name, att.text_enc, att.text_method = "", b"", "purged"
                    att.save(update_fields=["blob_name", "text_enc", "text_method"])
                messages.update(body_enc=b"")
                if count:
                    record(tenant, "retention.purged", f"Redacted {count} expired source document(s) and original email bodies",
                           details={"documents": count})
            # Keep a durable retry queue if a filesystem deletion fails. Never
            # remove a legacy hash-named blob while an active attachment uses it.
            for pending in PendingBlobDeletion.objects.filter(tenant=tenant):
                if Attachment.objects.filter(tenant=tenant, blob_name=pending.blob_name).exists():
                    continue
                try:
                    (tenant.vault_dir / pending.blob_name).unlink(missing_ok=True)
                except OSError:
                    self.stderr.write(f"{tenant.slug}: a blob deletion will be retried")
                else:
                    pending.delete()
            self.stdout.write(f"{tenant.slug}: purged {count} document(s)")
