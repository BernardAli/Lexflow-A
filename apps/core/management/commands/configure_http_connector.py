"""Provision a versioned HTTP connector from operator-controlled configuration."""
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.audit.services import record
from apps.connectors.models import Connector
from apps.connectors.protocol import endpoint
from apps.core.models import Tenant


class Command(BaseCommand):
    help = "Configure an authenticated HTTP connector using an operator endpoint key and private secret file."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--endpoint-key", required=True)
        parser.add_argument("--destination", required=True)
        parser.add_argument("--secret-file", type=Path, required=True)

    @transaction.atomic
    def handle(self, *args, **opts):
        if settings.LEXFLOW_DEPLOYMENT != "dedicated":
            raise CommandError("Configure the dedicated office boundary first.")
        try:
            office = Tenant.objects.select_for_update().get(slug=settings.LEXFLOW_TENANT_SLUG)
            path = opts["secret_file"]
            if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
                raise CommandError("Use a private regular secret file (mode 0600).")
            existing = Connector.objects.filter(tenant=office, name=opts["name"]).first()
            if existing and existing.transfers.exists():
                raise CommandError("This connector already has exports. Use a new versioned name to change its contract or secret.")
            connector = existing or Connector(tenant=office)
            connector.name, connector.kind, connector.delivery = opts["name"], "json", "webhook"
            connector.endpoint_key, connector.destination = opts["endpoint_key"], opts["destination"]
            connector.signing_secret = path.read_text().strip()
            connector.description = "Authenticated LexFlow handoff v1; destination acceptance must be validated."
            connector.full_clean()
            endpoint(connector)
            connector.save()
            record(office, "connector.configured", "Operator configured an authenticated HTTP destination",
                   object_type="connector", object_ref=str(connector.pk),
                   details={"endpoint_key": connector.endpoint_key, "connector": connector.name})
        except (Tenant.DoesNotExist, OSError, ValidationError) as exc:
            raise CommandError("Connector configuration failed. Check the dedicated office, private secret file and HTTPS endpoint map.") from exc
        self.stdout.write("HTTP connector configured. Validate the destination's schema and signed receipt before live use.")
