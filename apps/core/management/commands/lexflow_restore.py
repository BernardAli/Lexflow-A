import json
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from apps.core.recovery import restore


class Command(BaseCommand):
    help = "Restore an encrypted snapshot to EMPTY storage and database; validate keys, bytes and audit."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--input", required=True)

    def handle(self, *args, **opts):
        try:
            self.stdout.write(json.dumps(restore(opts["input"]), indent=2))
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc
