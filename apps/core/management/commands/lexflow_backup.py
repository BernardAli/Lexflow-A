import json
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from apps.core.recovery import create


class Command(BaseCommand):
    help = "Create an encrypted dedicated-office maintenance snapshot. Stop/drain all writers first."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True)

    def handle(self, *args, **opts):
        try:
            self.stdout.write(json.dumps(create(opts["output"]), indent=2))
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc
