"""Install a versioned destination mapping from an operator-owned JSON file."""
import json
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.connectors.models import Connector
from apps.connectors.profiles import configure_profile
from apps.core.models import Tenant


class Command(BaseCommand):
    help = "Create an immutable mapping revision and bind it to one office connector."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--connector", required=True, type=int)
        parser.add_argument("--spec", required=True, type=Path)

    def handle(self, *args, **options):
        if settings.LEXFLOW_DEPLOYMENT == "dedicated" and options["tenant"] != settings.LEXFLOW_TENANT_SLUG:
            raise CommandError("Select the configured dedicated office.")
        try:
            office = Tenant.objects.get(slug=options["tenant"])
            connector = Connector.objects.get(tenant=office, pk=options["connector"])
            with options["spec"].open("rb") as handle:
                encoded = handle.read(65_537)
            if len(encoded) > 65_536:
                raise CommandError("A mapping specification must be at most 64 KiB.")
            spec = json.loads(encoded)
            profile = configure_profile(office, connector, spec)
        except (Tenant.DoesNotExist, Connector.DoesNotExist):
            raise CommandError("Select an existing office and one of its connectors.")
        except (OSError, ValueError, UnicodeError):
            raise CommandError("Read a valid UTF-8 JSON mapping specification.")
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages))
        self.stdout.write(f"Installed {profile.key} v{profile.version}. New approvals use this revision; existing approvals are unchanged.")
