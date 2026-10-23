#!/usr/bin/env python3
"""Reproducible synthetic acceptance drill in new databases and temporary storage.

Checks real loopback HTTP transport, the remote intake client, encrypted recovery
and extraction. --postgres creates two NEW randomly named databases using the
operator's PostgreSQL connection environment; it never reuses an existing one.
The databases remain for inspection; this tool never drops databases.
"""
import argparse
from email.message import EmailMessage
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent


def command(env, *args):
    result = subprocess.run([sys.executable, "manage.py", *args], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=240)
    if result.returncode:
        raise RuntimeError(result.stderr + result.stdout)
    return result.stdout


def shell(env, code):
    return json.loads(command(env, "shell", "--no-imports", "-c", code).strip())


def port():
    with socket.socket() as handle:
        handle.bind(("127.0.0.1", 0))
        return handle.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    local_ocr = ROOT / ".pilot-tools/bin/tesseract"
    tesseract = os.environ.get("LEXFLOW_TESSERACT") or (str(local_ocr) if local_ocr.exists() else shutil.which("tesseract"))
    if not tesseract:
        parser.error("Install Tesseract with German/English language packs and Poppler before the complete acceptance drill.")
    runtime = Path(tempfile.mkdtemp(prefix="lexflow-acceptance-"))
    master, backup_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    env = dict(os.environ, DJANGO_DEBUG="1", LEXFLOW_DEMO_MODE="1", LEXFLOW_DEPLOYMENT="dedicated",
               LEXFLOW_TENANT_SLUG="acceptance", LEXFLOW_AI_PROVIDER="rules", DJANGO_SECRET_KEY=secrets.token_urlsafe(64),
               LEXFLOW_MASTER_KEY="fernet:" + master, LEXFLOW_BACKUP_KEY=backup_key,
               LEXFLOW_DB_PATH=str(runtime / "office.sqlite3"), LEXFLOW_VAULT_ROOT=str(runtime / "vault"),
               LEXFLOW_EXPORT_ROOT=str(runtime / "exports"), LEXFLOW_WEBHOOK_ALLOW_LOOPBACK="1",
               USE_POSTGRES="1" if args.postgres else "0", DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost,testserver",
               LEXFLOW_OCR_ENABLED="1", LEXFLOW_TESSERACT=tesseract)
    if args.postgres:
        import psycopg
        from psycopg import sql
        database = "lexflow_accept_" + secrets.token_hex(6)
        with psycopg.connect(host=env.get("POSTGRES_HOST", "localhost"), port=env.get("POSTGRES_PORT", "5432"),
                user=env.get("POSTGRES_USER", "lexflow"), password=env.get("POSTGRES_PASSWORD", ""),
                dbname="postgres", autocommit=True) as db:
            for name in (database, database + "_restore"):
                db.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        env["POSTGRES_DB"] = database
    private = runtime / "environment.json"
    fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump({key: value for key, value in env.items() if key.startswith(("LEXFLOW_", "DJANGO_", "POSTGRES_", "USE_POSTGRES"))}, handle)
    receiver_secret = secrets.token_urlsafe(48)
    secret_file = runtime / "receiver.key"
    fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(receiver_secret)
    receiver = subprocess.Popen([sys.executable, "integrations/reference_receiver.py", "--database", str(runtime / "receiver.sqlite3"),
        "--secret-file", str(secret_file), "--port", "0"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    app = None
    try:
        ready = json.loads(receiver.stdout.readline())
        env["LEXFLOW_WEBHOOK_ENDPOINTS"] = json.dumps({"reference": f'http://127.0.0.1:{ready["port"]}/v1/handoffs'})
        env["ACCEPTANCE_RECEIVER_SECRET"] = receiver_secret
        command(env, "migrate", "--noinput")
        fixture = shell(env, '''
import json, os
from apps.core.testing import make_office, property_matter
from apps.core.models import Membership
from apps.matters import services
from apps.connectors.models import Connector
from apps.connectors.services import transfer, deliver_webhook
office, users = make_office("acceptance", "Synthetic acceptance office")
matter = property_matter(office, blatt_contract="812")
matter.message.is_demo = True
matter.message.save(update_fields=["is_demo"])
matter.is_demo = True
matter.save(update_fields=["is_demo"])
services.confirm_field(matter.fields.get(definition__key="purchase_price"), users["clerk"])
services.approve(matter, Membership.objects.get(tenant=office, user=users["notary"]))
connector = Connector.objects.get(tenant=office, kind="json")
connector.delivery, connector.endpoint_key, connector.signing_secret = "webhook", "reference", os.environ["ACCEPTANCE_RECEIVER_SECRET"]
connector.save()
exported = transfer(matter, connector, users["notary"])
result = deliver_webhook(exported, users["notary"])
assert result.status == "delivered", result.error
again = deliver_webhook(result, users["notary"])
assert again.delivery_attempts == 1
print(json.dumps({"office":str(office.uid),"matter":matter.ref,"transfer":result.ref,"checksum":result.checksum,
                  "approval_hash":matter.approval_hash,"receipt":result.receipt_reference,"token":office.issue_api_token()}))
''')
        with sqlite3.connect(runtime / "receiver.sqlite3") as destination:
            remote = destination.execute("SELECT checksum, payload, receipt FROM handoffs").fetchall()
        assert len(remote) == 1 and hashlib.sha256(remote[0][1]).hexdigest() == fixture["checksum"] == remote[0][0]
        assert json.loads(remote[0][2])["approval_hash"] == fixture["approval_hash"]
        # Exercise the thin client against a separate, actual Django HTTP server.
        scanner = runtime / "scanner"
        scanner.mkdir(mode=0o700)
        email = EmailMessage()
        email["Subject"], email["From"] = "Synthetic Kaufvertrag intake", "Synthetic Office <demo@example.invalid>"
        email.set_content("Käufer: Clara Beispiel\nKaufpreis: 310.000,00 EUR\nGrundbuch: Berlin Blatt 102")
        email.add_attachment(b"Verkaeufer: Max Muster", maintype="text", subtype="plain", filename="synthetic.txt")
        (scanner / "sample.eml").write_bytes(email.as_bytes())
        token_file = runtime / "intake.key"
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(fixture.pop("token"))
        app_port = port()
        with (runtime / "app.log").open("w") as log:
            app = subprocess.Popen([sys.executable, "manage.py", "runserver", f"127.0.0.1:{app_port}", "--noreload"],
                                   cwd=ROOT, env=env, stdout=log, stderr=log)
            for _ in range(100):
                try:
                    with urlopen(f"http://127.0.0.1:{app_port}/login/", timeout=1) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if app.poll() is not None:
                        raise RuntimeError("The isolated acceptance app did not start")
                    time.sleep(.1)
            else:
                raise RuntimeError("The isolated acceptance app did not become ready")
            agent = [sys.executable, "tools/scanner_agent.py", "--folder", str(scanner), "--office", f"http://127.0.0.1:{app_port}",
                     "--token-file", str(token_file), "--state", str(runtime / "agent-state.json"), "--minimum-age", "0", "--allow-localhost"]
            first = json.loads(subprocess.check_output(agent, cwd=ROOT, text=True, timeout=60))
            second = json.loads(subprocess.check_output(agent, cwd=ROOT, text=True, timeout=60))
            assert first["accepted"] == 1 and second["already_sent"] == 1
            # Lose the local state: the API must still deduplicate the same email.
            (runtime / "agent-state.json").unlink()
            third = json.loads(subprocess.check_output(agent, cwd=ROOT, text=True, timeout=60))
            assert third["accepted"] == 1
            app.terminate()
            app.wait(timeout=10)
            app = None
        totals = shell(env, '''
import json
from apps.intake.models import IntakeMessage
from apps.audit.services import verify_chain
from apps.core.models import Tenant
assert IntakeMessage.objects.count() == 2
assert verify_chain(Tenant.objects.get()).ok
print(json.dumps({"intakes":IntakeMessage.objects.count(), "audit_verified":True}))
''')
        maintenance = dict(env, LEXFLOW_MAINTENANCE="1")
        checkpoint = runtime / "external-checkpoint.json"
        command(maintenance, "lexflow_audit_checkpoint", "--output", str(checkpoint))
        backup = runtime / "office.lfxbackup"
        snapshot = json.loads(command(maintenance, "lexflow_backup", "--output", str(backup)))
        restored_env = dict(maintenance, LEXFLOW_DB_PATH=str(runtime / "restored.sqlite3"),
                            LEXFLOW_VAULT_ROOT=str(runtime / "restored-vault"), LEXFLOW_EXPORT_ROOT=str(runtime / "restored-exports"))
        if args.postgres:
            restored_env["POSTGRES_DB"] += "_restore"
        restore_started = time.monotonic()
        recovery = json.loads(command(restored_env, "lexflow_restore", "--input", str(backup)))
        recovery["elapsed_seconds"] = round(time.monotonic() - restore_started, 3)
        recovery["external_checkpoint"] = json.loads(command(restored_env, "lexflow_audit_checkpoint", "--verify-against", str(checkpoint)))
        assert recovery["restored"] and recovery["audit_seq"] == snapshot["audit"]["seq"]
        evaluation = json.loads(command(env, "lexflow_evaluate", "--tenant", "acceptance", "--dataset", "evaluation/property_cases.json"))
        assert evaluation["accepted"]
        report = {"passed": True, "data_class": "synthetic", "database": "postgresql" if args.postgres else "sqlite",
                  "handoff": {"actual_http": True, "independent_receiver": True, "remote_records": len(remote), **fixture},
                  "scanner_agent": {"first_run": first, "repeat": second, "lost_state_retry": third, **totals},
                  "recovery": recovery, "evaluation": evaluation,
                  "limits": ["Loopback reference receiver; not a vendor integration", "Local runtime; not hosted TLS/container acceptance",
                             "Synthetic documents; not real-office accuracy or savings"], "private_runtime": str(runtime)}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"passed":True, "database":report["database"], "report":str(args.output),
                          "evaluation_assertions":evaluation["assertions"], "recovery":recovery}))
    finally:
        if app is not None:
            app.terminate()
            app.wait(timeout=10)
        receiver.terminate()
        receiver.wait(timeout=10)


if __name__ == "__main__":
    main()
