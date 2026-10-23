import json
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from apps.audit.checkpoints import export_checkpoint, verify_checkpoint
from apps.core.models import Tenant


class Command(BaseCommand):
    help = "Export a signed checkpoint for separate custody, or compare the database against a trusted external checkpoint."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", default=settings.LEXFLOW_TENANT_SLUG)
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--output")
        group.add_argument("--verify-against")

    def handle(self, *args, **opts):
        try:
            office = Tenant.objects.get(slug=opts["tenant"])
            result = (export_checkpoint(office, opts["output"]) if opts["output"] else
                      verify_checkpoint(office, opts["verify_against"]))
            self.stdout.write(json.dumps(result, indent=2))
        except (Tenant.DoesNotExist, ValidationError, OSError) as exc:
            raise CommandError(str(exc)) from exc
