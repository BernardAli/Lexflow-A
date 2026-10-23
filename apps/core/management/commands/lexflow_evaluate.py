import json
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from apps.core.evaluation import evaluate
from apps.core.models import Tenant


class Command(BaseCommand):
    help = "Evaluate a local sanitized/synthetic dataset without creating case records or calling external AI."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--dataset", required=True)

    def handle(self, *args, **opts):
        try:
            result = evaluate(Tenant.objects.get(slug=opts["tenant"]), opts["dataset"])
        except (ValidationError, Tenant.DoesNotExist, ValueError, KeyError, OSError) as exc:
            raise CommandError("Evaluation could not run; check the local dataset and office workflow.") from exc
        self.stdout.write(json.dumps(result, indent=2))
        if not result["accepted"]:
            raise CommandError("Evaluation acceptance failed. Inspect the failed case/field keys above.")
