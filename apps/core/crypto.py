"""Envelope encryption: one data key per tenant, wrapped by a master key.

The master key comes from LEXFLOW_MASTER_KEY. In development it is derived from
SECRET_KEY so the prototype runs without setup. In production, set a real key
(ideally sourced from a KMS/HSM) and rotate tenant keys independently.
"""
import base64
import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet
from django.conf import settings


@lru_cache(maxsize=4)
def _master(key_material: str) -> Fernet:
    if key_material.startswith("fernet:"):
        return Fernet(key_material[len("fernet:"):].encode())
    digest = hashlib.sha256(("lexflow-master|" + key_material).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def master_cipher() -> Fernet:
    return _master(settings.LEXFLOW_MASTER_KEY or settings.SECRET_KEY)


def generate_wrapped_key() -> str:
    return master_cipher().encrypt(Fernet.generate_key()).decode()


class TenantCipher:
    def __init__(self, wrapped: str):
        raw_key = master_cipher().decrypt(wrapped.encode())
        self._signing_key = hashlib.sha256(b"lexflow-audit-checkpoint|" + raw_key).digest()
        self._fernet = Fernet(raw_key)
        self.fingerprint = hashlib.sha256(raw_key).hexdigest()[:16]

    def encrypt(self, data: bytes) -> bytes:
        return self._fernet.encrypt(data)

    def sign_checkpoint(self, tenant_uid, seq, head):
        content = f"{tenant_uid}|{seq}|{head}".encode()
        return hmac.new(self._signing_key, content, hashlib.sha256).hexdigest()

    def decrypt(self, token: bytes) -> bytes:
        return self._fernet.decrypt(bytes(token))

    def encrypt_text(self, text: str) -> bytes:
        return self.encrypt((text or "").encode("utf-8"))

    def decrypt_text(self, token) -> str:
        if not token:
            return ""
        return self.decrypt(token).decode("utf-8")
