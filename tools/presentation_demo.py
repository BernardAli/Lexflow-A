#!/usr/bin/env python3
"""Prepare/run an isolated one-office synthetic presentation demo.

Existing project data is never used. --reset archives an earlier synthetic demo
under /tmp before preparing a fresh walkthrough. Secrets are local, mode 0600.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / ".pilot-demo"
PYTHON = ROOT / ".venv/bin/python"


def environment():
    DEMO.mkdir(mode=0o700, exist_ok=True)
    secret_file = DEMO / "secrets.json"
    if secret_file.exists():
        material = json.loads(secret_file.read_text())
    else:
        material = {"secret": secrets.token_urlsafe(64), "master": "fernet:" + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}
        fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(material, handle)
    local_ocr = ROOT / ".pilot-tools/bin/tesseract"
    tesseract = os.environ.get("LEXFLOW_TESSERACT") or (str(local_ocr) if local_ocr.exists() else shutil.which("tesseract"))
    return dict(os.environ,
                USE_POSTGRES="0", DJANGO_DEBUG="1", DJANGO_SECRET_KEY=material["secret"], LEXFLOW_MASTER_KEY=material["master"],
                LEXFLOW_DEPLOYMENT="dedicated", LEXFLOW_TENANT_SLUG="keller", LEXFLOW_DEMO_MODE="1",
                LEXFLOW_AI_PROVIDER="rules", LEXFLOW_DB_PATH=str(DEMO / "db.sqlite3"),
                LEXFLOW_VAULT_ROOT=str(DEMO / "vault"), LEXFLOW_EXPORT_ROOT=str(DEMO / "exports"),
                DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1,testserver",
                LEXFLOW_TESSERACT=tesseract or "tesseract",
                LEXFLOW_OCR_ENABLED=os.environ.get("LEXFLOW_OCR_ENABLED", "1" if tesseract else "0"))


def command(env, *args):
    result = subprocess.run([str(PYTHON), "manage.py", *args], cwd=ROOT, env=env, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--http-handoff", action="store_true", help="Start the independent synthetic HTTP receiver")
    opts = parser.parse_args()
    if not PYTHON.exists():
        parser.error("Create .venv and install requirements.txt first.")
    if opts.reset and DEMO.exists():
        archive = Path(tempfile.mkdtemp(prefix="lexflow-demo-archive-")) / "previous-demo"
        shutil.move(str(DEMO), archive)
        print(f"Earlier synthetic demo archived at {archive}", flush=True)
    env = environment()
    command(env, "migrate", "--noinput")
    command(env, "seed_lexflow", "--single-office")
    receiver = None
    try:
        if opts.http_handoff and not opts.prepare_only:
            secret_file = DEMO / "receiver.key"
            if not secret_file.exists():
                fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as handle:
                    handle.write(secrets.token_urlsafe(48))
            env["LEXFLOW_REFERENCE_SECRET"] = secret_file.read_text().strip()
            receiver = subprocess.Popen([str(PYTHON), "integrations/reference_receiver.py", "--database", str(DEMO / "receiver.sqlite3"),
                "--secret-file", str(secret_file), "--port", "0"], cwd=ROOT, stdout=subprocess.PIPE, text=True)
            ready = json.loads(receiver.stdout.readline())
            env["LEXFLOW_WEBHOOK_ALLOW_LOOPBACK"] = "1"
            env["LEXFLOW_WEBHOOK_ENDPOINTS"] = json.dumps({"reference": f'http://127.0.0.1:{ready["port"]}/v1/handoffs'})
            print("Independent synthetic HTTP receiver started. Select HTTP reference receiver after approval.", flush=True)
        features = command(env, "prepare_presentation_features")
        report = command(env, "lexflow_pilot_check", "--demo", "--json")
        (DEMO / "readiness.json").write_text(report)
        print(f"Dedicated one-office presentation demo ready: http://127.0.0.1:{opts.port}/presentation/", flush=True)
        print("Synthetic accounts: keller.clerk / keller.notar / admin. Password: lexflow-demo-2026", flush=True)
        print("Optional examples: " + features.strip(), flush=True)
        print("Existing db.sqlite3 and vault/ are separate from this demonstration.", flush=True)
        if not opts.prepare_only:
            raise SystemExit(subprocess.call([str(PYTHON), "manage.py", "runserver", f"127.0.0.1:{opts.port}", "--noreload"], cwd=ROOT, env=env))
    finally:
        if receiver is not None:
            receiver.terminate()
            receiver.wait(timeout=10)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nPresentation demo stopped.")
