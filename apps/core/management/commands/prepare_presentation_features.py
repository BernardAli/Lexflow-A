"""Add optional scan/HTTP examples only inside the isolated synthetic demo."""
import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.connectors.models import Connector
from apps.core.evaluation import document_bytes
from apps.core.models import Tenant
from apps.matters.engine.ocr import available
from apps.matters.services import ingest_message


class Command(BaseCommand):
    help = "Prepare optional synthetic scan and reference-HTTP presentation examples."

    def handle(self, *args, **opts):
        if not settings.LEXFLOW_DEMO_MODE or settings.LEXFLOW_DEPLOYMENT != "dedicated":
            raise CommandError("Presentation preparation requires the isolated dedicated demonstration.")
        office = Tenant.objects.get(slug=settings.LEXFLOW_TENANT_SLUG)
        if Tenant.objects.count() != 1:
            raise CommandError("Use an isolated single-office demonstration database.")
        secret = os.environ.get("LEXFLOW_REFERENCE_SECRET", "")
        if secret:
            if len(secret) < 32:
                raise CommandError("A reference receiver secret needs at least 32 characters.")
            Connector.objects.update_or_create(tenant=office, name="HTTP reference receiver", defaults={
                "kind": "json", "delivery": "webhook", "tier": "import", "endpoint_key": "reference",
                "signing_secret": secret, "destination": "Independent synthetic reference receiver",
                "description": "Actual authenticated HTTP transport with a signed receipt; no vendor connection.", "is_active": True})
        else:
            Connector.objects.filter(tenant=office, name="HTTP reference receiver", endpoint_key="reference").update(is_active=False)
        scan = None
        if settings.LEXFLOW_OCR_ENABLED and available():
            corpus = json.loads((settings.BASE_DIR / "evaluation/property_cases.json").read_text())
            example = corpus["cases"][0]["documents"][0]
            filename, content_type, data = document_bytes({**example, "format": "scanned_pdf"}, settings.BASE_DIR)
            scan = ingest_message(office, subject="Synthetic scanned property purchase", files=[(filename, content_type, data)],
                                  external_id="demo-scanned-purchase", is_demo=True)
        self.stdout.write(json.dumps({"scan": scan.ref if scan else None, "http_configured": bool(secret)}))
