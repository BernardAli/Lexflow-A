"""Fail-closed readiness checks, separate for synthetic demo and real deployment."""
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.audit.services import verify_chain
from apps.connectors.models import Connector, Transfer
from apps.core.models import Tenant
from apps.intake.models import IntakeMessage, PendingBlobDeletion
from apps.matters.models import Matter
from apps.matters.engine.ocr import available as ocr_available
from apps.connectors.protocol import endpoint
from django.core.exceptions import ValidationError


class Command(BaseCommand):
    help = "Check the dedicated presentation demo or live pilot readiness."

    def add_arguments(self, parser):
        parser.add_argument("--demo", action="store_true")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **opts):
        errors = []
        offices = list(Tenant.objects.all())
        if len(offices) != 1:
            errors.append("Use an isolated database containing exactly one office.")
        if settings.LEXFLOW_DEPLOYMENT != "dedicated" or not settings.LEXFLOW_TENANT_SLUG:
            errors.append("Configure a dedicated deployment and its office slug.")
        for office in offices:
            if office.slug != settings.LEXFLOW_TENANT_SLUG:
                errors.append("The database office differs from the configured office.")
            if not verify_chain(office).ok:
                errors.append("The signed audit chain does not verify.")
            if Matter.objects.filter(tenant=office, status__in=["approved", "transferred"], approval_hash="").exists():
                errors.append("Earlier approvals need to be reopened and approved with frozen snapshots.")
        if PendingBlobDeletion.objects.exists():
            errors.append("Retention has pending blob deletions; complete or investigate them.")
        if settings.LEXFLOW_OCR_ENABLED and not ocr_available():
            errors.append("Local OCR is enabled but the binaries or configured language packs are unavailable.")
        if not opts["demo"]:
            for connector in Connector.objects.filter(delivery="webhook", is_active=True):
                try:
                    endpoint(connector)
                except ValidationError:
                    errors.append("An active HTTP connector lacks a valid HTTPS endpoint or signing secret.")
        if opts["demo"]:
            if not settings.LEXFLOW_DEMO_MODE:
                errors.append("The presentation demo requires explicit demo mode.")
            if IntakeMessage.objects.filter(is_demo=False).exists():
                errors.append("The presentation database must contain synthetic demonstration intakes only.")
            if not Connector.objects.filter(kind="xml", delivery="folder", is_active=True).exists():
                errors.append("Configure the demonstration XML import-folder connector.")
        else:
            if settings.DEBUG or settings.LEXFLOW_DEMO_MODE:
                errors.append("Disable development/debug and demonstration modes.")
            if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
                errors.append("Use the dedicated PostgreSQL deployment for the live pilot's locking guarantees.")
            if IntakeMessage.objects.filter(is_demo=True).exists():
                errors.append("Use a clean live database without seeded demonstration intakes.")
            if any(office.baseline_recorded_at is None for office in offices):
                errors.append("Record an agreed office baseline before measuring the pilot.")
            if not Transfer.objects.filter(status="delivered", matter__is_demo=False).exists():
                errors.append("Record a validated destination receipt for a sanitized live-pilot acceptance case.")
        result = {"mode": "presentation_demo" if opts["demo"] else "live_pilot", "ready": not errors,
                  "errors": errors, "office_count": len(offices),
                  "scope": "Property purchase; digital PDF/text and optional local OCR; approved file/HTTP handoff with receipt",
                  "local_ocr_enabled": settings.LEXFLOW_OCR_ENABLED,
                  "external_acceptance": "Vendor schema, hosting/operations and client acceptance must be validated separately."}
        if opts["json"]:
            self.stdout.write(json.dumps(result, indent=2))
        else:
            self.stdout.write("Ready" if result["ready"] else "Readiness checks failed")
            for error in errors:
                self.stdout.write("- " + error)
        if errors:
            raise CommandError("Readiness conditions are not satisfied.")
