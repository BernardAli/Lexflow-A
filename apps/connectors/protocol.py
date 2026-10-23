"""LexFlow handoff v1: authenticated requests and bound, signed receipts."""
import hashlib
import hmac
import http.client
import ipaddress
import json
import ssl
import time
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError

MAX_RECEIPT_BYTES = 16_384


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def request_signature(secret, timestamp, key, checksum, approval, transfer):
    content = f"lexflow-request-v1\n{timestamp}\n{key}\n{checksum}\n{approval}\n{transfer}".encode()
    return hmac.new(secret.encode(), content, hashlib.sha256).hexdigest()


def receipt_signature(secret, receipt):
    return hmac.new(secret.encode(), b"lexflow-receipt-v1\n" + canonical(receipt), hashlib.sha256).hexdigest()


def endpoint(connector):
    target = settings.LEXFLOW_WEBHOOK_ENDPOINTS.get(connector.endpoint_key, "")
    try:
        url = urlsplit(target)
        port = url.port
        local = url.hostname == "localhost"
        try:
            local |= ipaddress.ip_address(url.hostname or "").is_loopback
        except ValueError:
            pass
        insecure_local = settings.LEXFLOW_WEBHOOK_ALLOW_LOOPBACK and local and url.scheme == "http"
        if (not url.hostname or url.username or url.password or url.fragment or url.query or
                url.scheme != "https" and not insecure_local):
            raise ValueError
        if len(connector.signing_secret) < 32:
            raise ValueError
    except (ValueError, TypeError):
        raise ValidationError("Configure an HTTPS endpoint key and a signing secret of at least 32 characters.")
    return url, port


def send(connector, exported, body):
    url, port = endpoint(connector)
    if exported.content_type != "application/json":
        raise ValidationError("The HTTP handoff requires the versioned JSON format.")
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json", "Accept": "application/json",
        "X-LexFlow-Version": "1", "X-LexFlow-Timestamp": timestamp,
        "Idempotency-Key": exported.idempotency_key, "X-LexFlow-Transfer": exported.ref,
        "X-LexFlow-SHA256": exported.checksum, "X-LexFlow-Approval": exported.approval_hash,
        "X-LexFlow-Signature": request_signature(connector.signing_secret, timestamp,
            exported.idempotency_key, exported.checksum, exported.approval_hash, exported.ref),
    }
    connection = (http.client.HTTPSConnection(url.hostname, port, context=ssl.create_default_context(),
                    timeout=settings.LEXFLOW_WEBHOOK_TIMEOUT) if url.scheme == "https" else
                  http.client.HTTPConnection(url.hostname, port, timeout=settings.LEXFLOW_WEBHOOK_TIMEOUT))
    try:
        connection.request("POST", url.path or "/", body.encode(), headers)
        response = connection.getresponse()
        encoded = response.read(MAX_RECEIPT_BYTES + 1)
        if response.status not in (200, 201) or len(encoded) > MAX_RECEIPT_BYTES:
            raise ValidationError("Destination did not return an accepted, bounded receipt. Retry after checking it.")
        envelope = json.loads(encoded)
        receipt, signature = envelope["receipt"], envelope["signature"]
        if not isinstance(receipt, dict) or not isinstance(signature, str):
            raise ValueError
        if not hmac.compare_digest(receipt_signature(connector.signing_secret, receipt), signature):
            raise ValueError
        expected = {"version": 1, "status": "accepted", "idempotency_key": exported.idempotency_key,
                    "sha256": exported.checksum, "approval_hash": exported.approval_hash, "transfer": exported.ref}
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise ValueError
        reference = receipt.get("reference")
        if not isinstance(reference, str) or not reference.strip() or len(reference) > 200:
            raise ValueError
        return receipt, encoded
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ValidationError("Destination receipt failed authentication or did not match this approved export.") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise ValidationError("Destination is unreachable or timed out. Retry uses the same idempotency key.") from exc
    finally:
        connection.close()
