"""Encrypt existing case data and seal verified legacy audit chains.

Legacy 'delivered' rows were local downloads. Preserve those exports but require
an explicit new approval/receipt before they can be treated as deliveries.
Back up the database and its matching vault/master key before applying.
"""
import hashlib
import json

from django.db import migrations
from django.db.models import Q


def harden_existing(apps, schema_editor):
    from apps.core.crypto import TenantCipher
    from apps.core.fields import PREFIX
    alias = schema_editor.connection.alias
    private_fields = {
        ("intake", "IntakeMessage"): ("subject", "sender_name", "sender_email"),
        ("intake", "Attachment"): ("filename",),
        ("matters", "Matter"): ("title",),
        ("matters", "ExtractedField"): ("value", "normalized", "note"),
        ("matters", "FieldCandidate"): ("value", "normalized"),
        ("matters", "FieldRevision"): ("old_value", "new_value"),
        ("connectors", "Connector"): ("signing_secret",),
        ("audit", "AuditEvent"): ("actor_label", "summary", "details"),
    }
    for (label, name), fields in private_fields.items():
        model = apps.get_model(label, name)
        for obj in model.objects.using(alias).all().iterator():
            # pre_save uses the owning tenant and removes all legacy plaintext.
            obj.save(using=alias, update_fields=fields)
    Tenant = apps.get_model("core", "Tenant")
    Event = apps.get_model("audit", "AuditEvent")
    for tenant in Tenant.objects.using(alias).all():
        previous, count = "0" * 64, 0
        for event in Event.objects.using(alias).filter(tenant_id=tenant.pk).order_by("seq"):
            count += 1
            canonical = json.dumps({
                "tenant": str(tenant.uid), "seq": event.seq, "at": event.created_at.isoformat(),
                "actor": event.actor_label, "action": event.action, "object_type": event.object_type,
                "object_ref": event.object_ref, "summary": event.summary, "details": event.details,
                "prev": event.prev_hash,
            }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            if event.seq != count or event.prev_hash != previous or hashlib.sha256(canonical.encode()).hexdigest() != event.hash:
                raise RuntimeError("Existing audit chain is invalid; investigate before sealing it.")
            previous = event.hash
        cipher = TenantCipher(tenant.data_key_wrapped)
        tenant.audit_seq, tenant.audit_head = count, previous
        tenant.audit_seal = cipher.sign_checkpoint(tenant.uid, count, previous)
        tenant.save(using=alias, update_fields=["audit_seq", "audit_head", "audit_seal"])
    Message = apps.get_model("intake", "IntakeMessage")
    Matter = apps.get_model("matters", "Matter")
    demo_ids = Message.objects.using(alias).filter(
        Q(external_id__startswith="demo-") | Q(external_id__startswith="keller-hist-") | Q(external_id__startswith="hoffmann-hist-")
    ).values_list("pk", flat=True)
    Message.objects.using(alias).filter(pk__in=demo_ids).update(is_demo=True)
    Matter.objects.using(alias).filter(message_id__in=demo_ids).update(is_demo=True)
    apps.get_model("connectors", "Transfer").objects.using(alias).filter(status="delivered").update(status="exported")
    Matter.objects.using(alias).filter(status="transferred").update(status="approved", transferred_at=None)
    apps.get_model("studio", "FieldDefinition").objects.using(alias).filter(
        key="managing_director", destination_path="Gesellschaft.Geschaeftsfuehrer"
    ).update(destination_path="Gesellschaft.Geschaeftsfuehrer.Name")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_tenant_audit_head_tenant_audit_seal_tenant_audit_seq_and_more"),
        ("audit", "0002_alter_auditevent_actor_label_and_more"),
        ("intake", "0002_pendingblobdeletion_attachment_reviewed_at_and_more"),
        ("matters", "0002_matter_approval_enc_matter_approval_hash_and_more"),
        ("connectors", "0002_connector_delivery_transfer_acknowledged_at_and_more"),
    ]
    operations = [migrations.RunPython(harden_existing)]
