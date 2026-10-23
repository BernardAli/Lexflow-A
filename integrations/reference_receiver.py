#!/usr/bin/env python3
"""Independent synthetic-only receiver for LexFlow handoff v1.

Uses Python's standard library, its own database, and a separately provisioned
shared secret. This is a contract-test destination, not a notary vendor adapter.
Put a real integration's receiver behind HTTPS; this fixture binds loopback.
"""
import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import time


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class Receiver(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # No document bodies, subject lines, tokens or names in access logs.

    def reply(self, status, value):
        body = canonical(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/v1/handoffs":
            return self.reply(404, {"error": "not_found"})
        self.connection.settimeout(10)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 2 * 1024 * 1024:
                return self.reply(413, {"error": "body_limit"})
            if self.headers.get("Content-Type") != "application/json" or self.headers.get("X-LexFlow-Version") != "1":
                return self.reply(400, {"error": "invalid_content_type_or_version"})
            body = self.rfile.read(size)
            timestamp = self.headers.get("X-LexFlow-Timestamp", "")
            key = self.headers.get("Idempotency-Key", "")
            checksum = self.headers.get("X-LexFlow-SHA256", "")
            approval = self.headers.get("X-LexFlow-Approval", "")
            transfer = self.headers.get("X-LexFlow-Transfer", "")
            if (abs(time.time() - int(timestamp)) > 300 or
                    not all(re.fullmatch(r"[a-f0-9]{64}", s) for s in (key, checksum, approval)) or
                    not re.fullmatch(r"[A-Za-z0-9-]{1,20}", transfer) or
                    hashlib.sha256(body).hexdigest() != checksum):
                return self.reply(401, {"error": "invalid_request"})
            signed = f"lexflow-request-v1\n{timestamp}\n{key}\n{checksum}\n{approval}\n{transfer}".encode()
            expected = hmac.new(self.server.secret.encode(), signed, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, self.headers.get("X-LexFlow-Signature", "")):
                return self.reply(401, {"error": "invalid_signature"})
            payload = json.loads(body)
            if (payload.get("schema") != "lexflow.handoff/1" or payload.get("data_class") != "synthetic" or
                    not payload.get("approved_at") or not payload.get("approved_by") or
                    not isinstance(payload.get("data"), dict) or not payload["data"]):
                return self.reply(422, {"error": "synthetic_approved_handoff_required"})
            with sqlite3.connect(self.server.database, timeout=10) as db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute("SELECT checksum, approval, transfer, receipt FROM handoffs WHERE key=?", (key,)).fetchone()
                if existing:
                    if existing[:3] != (checksum, approval, transfer):
                        return self.reply(409, {"error": "idempotency_conflict"})
                    receipt = json.loads(existing[3])
                else:
                    receipt = {"version": 1, "status": "accepted", "idempotency_key": key,
                               "sha256": checksum, "approval_hash": approval, "transfer": transfer,
                               "reference": "REF-" + key[:24], "received_at": int(time.time()),
                               "destination": "synthetic-reference-receiver"}
                    db.execute("INSERT INTO handoffs VALUES (?,?,?,?,?,?)",
                               (key, checksum, approval, transfer, body, canonical(receipt).decode()))
                db.commit()  # A receipt is never returned before durable acceptance.
            signature = hmac.new(self.server.secret.encode(), b"lexflow-receipt-v1\n" + canonical(receipt),
                                 hashlib.sha256).hexdigest()
            self.reply(200 if existing else 201, {"receipt": receipt, "signature": signature})
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            self.reply(400, {"error": "invalid_request"})
        except (OSError, sqlite3.Error):
            self.reply(503, {"error": "receiver_unavailable"})


def server(database, secret, port=0):
    if len(secret) < 32:
        raise ValueError("Use a shared secret of at least 32 characters")
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(database, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    database.chmod(0o600)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE IF NOT EXISTS handoffs (key TEXT PRIMARY KEY, checksum TEXT, approval TEXT, transfer TEXT, payload BLOB, receipt TEXT)")
    instance = ThreadingHTTPServer(("127.0.0.1", port), Receiver)
    instance.secret, instance.database = secret, str(database)
    return instance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()
    if args.secret_file.stat().st_mode & 0o077:
        parser.error("The secret file must be private (mode 0600)")
    instance = server(args.database, args.secret_file.read_text().strip(), args.port)
    print(json.dumps({"ready": True, "port": instance.server_port, "scope": "synthetic-only"}), flush=True)
    try:
        instance.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        instance.server_close()


if __name__ == "__main__":
    main()
