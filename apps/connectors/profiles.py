"""Tenant-scoped configuration and approval-time destination bindings."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from apps.audit.services import record
from apps.core.models import Tenant
from apps.studio.models import MatterType

from .mapping import fingerprint
from .models import Connector, MappingProfile


def freeze_profiles(matter):
    profiles = {}
    for connector in Connector.objects.filter(tenant=matter.tenant, is_active=True,
            mapping_profile__matter_type=matter.matter_type).select_related("mapping_profile__matter_type"):
        connector.clean()
        profiles[str(connector.pk)] = {**connector.mapping_profile.snapshot(), "kind": connector.kind}
    return profiles


def profile_for_snapshot(connector, snapshot):
    profile = snapshot.get("mapping_profiles", {}).get(str(connector.pk))
    if profile:
        original = {key: value for key, value in profile.items() if key not in ("digest", "kind")}
        if fingerprint(original) != profile["digest"]:
            raise ValidationError("The frozen mapping failed its integrity check.")
        if connector.kind != profile["kind"]:
            raise ValidationError("The connector format changed. Restore its approved format or reopen the case.")
        if profile["matter_type"] != snapshot["matter_type"]:
            raise ValidationError("This mapping does not support the case's workflow.")
        return profile
    if connector.mapping_profile_id:
        connector.clean()
        raise ValidationError("This mapping was not included in the approval. Select a matching workflow connector, or reopen and approve again.")
    return None


@transaction.atomic
def configure_profile(office, connector, spec, *, actor=None):
    """Create a new immutable revision; old approvals retain their own mapping."""
    Tenant.objects.select_for_update().get(pk=office.pk)
    connector = Connector.objects.get(pk=connector.pk, tenant=office)
    allowed = {"key", "name", "matter_type", "adapter", "target_version", "specification", "rules"}
    if not isinstance(spec, dict) or set(spec) - allowed:
        raise ValidationError("Unsupported mapping profile keys.")
    if any(not isinstance(value, str) for key, value in spec.items() if key != "rules"):
        raise ValidationError("Profile metadata must be text.")
    try:
        workflow = MatterType.objects.get(tenant=office, key=spec["matter_type"])
        key, name = spec["key"], spec["name"]
    except (KeyError, MatterType.DoesNotExist, TypeError, ValueError):
        raise ValidationError("Specify a profile key, name and workflow belonging to this office.")
    version = (MappingProfile.objects.filter(tenant=office, key=key).aggregate(n=Max("version"))["n"] or 0) + 1
    profile = MappingProfile(tenant=office, key=key, version=version, name=name, matter_type=workflow,
                             adapter_key=spec.get("adapter", "canonical-json-v1"),
                             target_version=spec.get("target_version", ""), specification=spec.get("specification", ""),
                             rules=spec.get("rules", []))
    profile.save()
    connector.mapping_profile = profile
    connector.full_clean()
    connector.save(update_fields=["mapping_profile"])
    record(office, "connector.mapping_configured", "Created a destination mapping revision", actor=actor,
           object_type="connector", object_ref=str(connector.pk),
           details={"profile": profile.key, "version": version, "digest": profile.snapshot()["digest"]})
    return profile


@transaction.atomic
def enable_case_exports(office, *, actor=None):
    Tenant.objects.select_for_update().get(pk=office.pk)
    created = []
    for workflow in MatterType.objects.filter(tenant=office, is_active=True):
        key = f"case-{workflow.key}"
        existing = Connector.objects.filter(tenant=office, mapping_profile__key=key,
                                             mapping_profile__adapter_key="canonical-json-v1").first()
        if existing:
            continue
        connector = Connector.objects.create(tenant=office, name=f"Case JSON — {workflow.name}"[:80], kind="json",
            destination="LexFlow adapter development", description="Complete reviewed case data. Not a ProNotar import file.")
        configure_profile(office, connector, {"key": key, "name": f"{workflow.name} case data",
                          "matter_type": workflow.key, "rules": []}, actor=actor)
        created.append(connector)
    return created
