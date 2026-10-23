#!/usr/bin/env python3
"""Send a scanner folder or saved .eml messages through the office intake API.

This thin client needs no database, vault or encryption-key access. Run once from
an office scheduler. Originals stay in place; a private state file tracks accepted
content. Retrying after a lost response reuses the same server idempotency key.
"""
import argparse
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
import hashlib
import http.client
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import secrets
import ssl
import time
from urllib.parse import urlsplit

TYPES = {".pdf", ".txt", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".eml"}
MAX_BYTES = 20 * 1024 * 1024


def intake(path):
    if path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise ValueError("A source is a symlink or exceeds the upload size limit")
    data = path.read_bytes()
    if not data or len(data) > MAX_BYTES:
        raise ValueError("A source is empty or exceeds the upload size limit")
    fields = {"subject": "Scanner intake: " + path.stem}
    files = [(path.name, mimetypes.guess_type(path.name)[0] or "application/octet-stream", data)]
    if path.suffix.lower() == ".eml":
        message = BytesParser(policy=policy.default).parsebytes(data)
        name, address = parseaddr(message.get("From", ""))
        body = message.get_body(preferencelist=("plain",))
        fields = {"subject": str(message.get("Subject", "Saved office email")), "sender_name": name,
                  "sender_email": address, "body": body.get_content() if body is not None else ""}
        files = []
        for index, part in enumerate(message.iter_attachments(), 1):
            filename = part.get_filename()
            if not filename:
                continue
            if Path(filename).name != filename or Path(filename).suffix.lower() not in TYPES - {".eml"}:
                raise ValueError("An email attachment has an unsupported filename or type")
            files.append((filename, part.get_content_type(), part.get_payload(decode=True) or b""))
        if not files and not fields["body"].strip():
            raise ValueError("The email has no supported attachments or plain-text body")
    fields["external_id"] = "agent-v1-" + hashlib.sha256(data).hexdigest()
    return fields, files


def multipart(fields, files):
    boundary = "lexflow-" + secrets.token_hex(24)
    result = bytearray()
    for key, value in fields.items():
        result.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    for filename, content_type, data in files:
        if any(c in filename for c in ('"', '\r', '\n', '\\')):
            raise ValueError("Invalid attachment filename")
        result.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode())
        result.extend(data)
        result.extend(b"\r\n")
    result.extend(f"--{boundary}--\r\n".encode())
    if len(files) > 20 or len(result) > 60 * 1024 * 1024:
        raise ValueError("Intake exceeds the file-count or total-size limit")
    return bytes(result), "multipart/form-data; boundary=" + boundary


def send(base_url, token, fields, files, *, allow_localhost=False):
    url = urlsplit(base_url)
    local = url.hostname == "localhost"
    try:
        local |= ipaddress.ip_address(url.hostname or "").is_loopback
    except ValueError:
        pass
    if (not url.hostname or url.username or url.password or url.query or url.fragment or
            url.path not in ("", "/") or url.scheme != "https" and not (allow_localhost and local and url.scheme == "http")):
        raise ValueError("Use the HTTPS office origin, or explicitly allow a loopback test origin")
    body, content_type = multipart(fields, files)
    connection = (http.client.HTTPSConnection(url.hostname, url.port, context=ssl.create_default_context(), timeout=120)
                  if url.scheme == "https" else http.client.HTTPConnection(url.hostname, url.port, timeout=120))
    try:
        connection.request("POST", "/api/v1/intake/", body=body,
                           headers={"Authorization": "Bearer " + token, "Content-Type": content_type})
        response = connection.getresponse()
        encoded = response.read(1024 * 1024 + 1)
        if len(encoded) > 1024 * 1024:
            raise ValueError("Office returned an oversized response")
        value = json.loads(encoded)
        if response.status != 201 or not value.get("message") or not value.get("matter"):
            raise ValueError("Office has not confirmed processed intake; keep the source and investigate or retry")
        return {"message": value["message"], "matter": value["matter"]["ref"]}
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--office", required=True, help="HTTPS origin of this office deployment")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--minimum-age", type=int, default=10, help="Skip files the scanner may still be writing")
    parser.add_argument("--allow-localhost", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.token_file.stat().st_mode & 0o077 or args.token_file.is_symlink():
        parser.error("Use a private regular token file (mode 0600)")
    if args.state.exists() and (args.state.stat().st_mode & 0o077 or args.state.is_symlink()):
        parser.error("Use a private regular state file (mode 0600)")
    token = args.token_file.read_text().strip()
    if not token.startswith("lfx_"):
        parser.error("Supply an office intake API token")
    state = json.loads(args.state.read_text()) if args.state.exists() else {}
    # Bind state to an endpoint and token: copying a state file to another office
    # cannot silently suppress its intake. No token is stored in the state file.
    scope = hashlib.sha256((args.office.rstrip("/") + "|" + token).encode()).hexdigest()
    accepted = state.get("accepted", {}) if state.get("scope") == scope else {}
    counts = {"accepted": 0, "already_sent": 0, "failed": 0, "dry_run": args.dry_run}
    for path in sorted(args.folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in TYPES or time.time() - path.stat().st_mtime < args.minimum_age:
            continue
        try:
            fields, files = intake(path)
            key = fields["external_id"]
            if key in accepted:
                counts["already_sent"] += 1
                continue
            if args.dry_run:
                counts["accepted"] += 1
                continue
            accepted[key] = send(args.office, token, fields, files, allow_localhost=args.allow_localhost)
            counts["accepted"] += 1
            args.state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = args.state.with_name(args.state.name + "." + secrets.token_hex(8))
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                json.dump({"scope": scope, "accepted": accepted}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, args.state)
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            counts["failed"] += 1
    print(json.dumps(counts))
    return bool(counts["failed"])


if __name__ == "__main__":
    raise SystemExit(main())
