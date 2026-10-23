"""Encrypted case fields. Nonempty ciphertext is never usable in SQL lookups.

Encryption is performed in pre_save, where the owning tenant is available.
Decryption is lazy and uses that same tenant's data key. Empty strings remain
empty so presence queries work without decrypting confidential values.
"""
import json

from django.db import models
from django.db.models.query_utils import DeferredAttribute

from .crypto import TenantCipher

PREFIX = "lfx:v1:"


class Ciphertext(str):
    pass


def tenant_for(instance):
    if getattr(instance, "tenant_id", None):
        return instance.tenant
    if getattr(instance, "matter_id", None):
        return instance.matter.tenant
    if getattr(instance, "field_id", None):
        return instance.field.matter.tenant
    raise ValueError("Encrypted case fields require an owning tenant.")


class SecretAttribute(DeferredAttribute):
    def __set__(self, instance, value):
        instance.__dict__[self.field.attname] = value

    def __get__(self, instance, cls=None):
        value = super().__get__(instance, cls)
        if instance is None:
            return value
        if isinstance(value, Ciphertext):
            tenant = tenant_for(instance)
            value = TenantCipher(tenant.data_key_wrapped).decrypt_text(value[len(PREFIX):].encode())
            if self.field.is_json:
                value = json.loads(value)
            instance.__dict__[self.field.attname] = value
        elif self.field.is_json and isinstance(value, str):
            value = json.loads(value)
            instance.__dict__[self.field.attname] = value
        return value


class EncryptedTextField(models.TextField):
    descriptor_class = SecretAttribute
    is_json = False

    def from_db_value(self, value, expression, connection):
        if value and value.startswith(PREFIX):
            return Ciphertext(value)
        return value

    def pre_save(self, model_instance, add):
        value = getattr(model_instance, self.attname)
        if self.is_json:
            value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if not value:
            return ""
        tenant = tenant_for(model_instance)
        token = TenantCipher(tenant.data_key_wrapped).encrypt_text(str(value)).decode()
        return Ciphertext(PREFIX + token)

    def get_prep_value(self, value):
        if value in (None, "") or isinstance(value, Ciphertext):
            return value
        raise ValueError("Confidential fields must be saved on a tenant-owned instance; SQL value searches/updates are disabled.")


class EncryptedJSONField(EncryptedTextField):
    is_json = True

