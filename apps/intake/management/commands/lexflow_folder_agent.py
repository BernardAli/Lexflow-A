"""Scanner / folder agent.

Watches a local folder (e.g. the scanner's network share) and submits files to
LexFlow. Each sub-folder becomes one intake message; loose files become one
message each. Processed items move into ".processed/" next to the originals.

    python manage.py lexflow_folder_agent --tenant keller --path ./scanner-inbox
    python manage.py lexflow_folder_agent --tenant keller --path ./scanner-inbox --watch 30
"""
import mimetypes
import hashlib
from django.conf import settings
from django.core.exceptions import ValidationError
import shutil
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Tenant
from apps.intake.models import IntakeMessage
from apps.matters.services import ingest_message

ALLOWED = {".pdf", ".txt", ".eml", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


class Command(BaseCommand):
    help = "Submit files from a scanner folder to LexFlow."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Office slug")
        parser.add_argument("--path", required=True)
        parser.add_argument("--watch", type=int, default=0, help="Poll every N seconds (0 = run once)")

    def handle(self, *args, **opts):
        tenant = Tenant.objects.filter(slug=opts["tenant"]).first()
        if not tenant:
            raise CommandError(f"No office with slug {opts['tenant']!r}")
        root = Path(opts["path"]).resolve()
        if not root.is_dir():
            raise CommandError(f"{root} is not a folder")
        while True:
            n = self.sweep(tenant, root)
            self.stdout.write(f"Submitted {n} message(s) from {root}")
            if not opts["watch"]:
                break
            time.sleep(opts["watch"])

    def sweep(self, tenant, root: Path) -> int:
        done = root / ".processed"
        done.mkdir(exist_ok=True)
        count = 0
        for item in sorted(root.iterdir()):
            if item.name.startswith("."):
                continue
            paths = sorted(p for p in item.iterdir() if p.suffix.lower() in ALLOWED) if item.is_dir() else (
                [item] if item.suffix.lower() in ALLOWED else [])
            if not paths:
                continue
            if (len(paths) > settings.LEXFLOW_MAX_FILES or
                sum(p.stat().st_size for p in paths) > settings.LEXFLOW_MAX_TOTAL_MB * 1024 * 1024 or
                any(p.stat().st_size > settings.LEXFLOW_MAX_UPLOAD_MB * 1024 * 1024 for p in paths) or
                any(not p.resolve().is_relative_to(root) for p in paths)) :
                self.stderr.write("An intake exceeded configured file limits and was left for review.")
                continue
            files = [(p.name, mimetypes.guess_type(p.name)[0] or "", p.read_bytes()) for p in paths if p.is_file()]
            external_id = "folder-" + hashlib.sha256((str(item.resolve()) + "|" + "|".join(hashlib.sha256(f[2]).hexdigest() for f in files)).encode()).hexdigest()
            try:
                msg = ingest_message(tenant, subject=item.stem.replace("_", " "), files=files,
                                     source=IntakeMessage.Source.FOLDER, sender_name="Folder agent",
                                     external_id=external_id, is_demo=settings.LEXFLOW_DEMO_MODE)
            except ValidationError:
                self.stderr.write("An intake failed validation and was left for review.")
                continue
            if msg.status == IntakeMessage.Status.FAILED:
                self.stderr.write("Processing failed; originals were retained for retry in LexFlow.")
            shutil.move(str(item), str(done / item.name))
            count += 1
        return count
