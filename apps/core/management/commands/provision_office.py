"""Provision one real office without demonstration accounts or data."""
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.audit.services import record
from apps.connectors.models import Connector
from apps.core.models import Membership, Tenant
from apps.studio.defaults import install_defaults


class Command(BaseCommand):
    help = "Provision a dedicated office; password comes from LEXFLOW_ADMIN_PASSWORD."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--admin", required=True)

    @transaction.atomic
    def handle(self, *args, **opts):
        if settings.LEXFLOW_DEPLOYMENT != "dedicated" or not settings.LEXFLOW_TENANT_SLUG:
            raise CommandError("Configure the dedicated deployment and its office slug first.")
        if Tenant.objects.exists():
            raise CommandError("This deployment already has an office. Provisioning will not replace it.")
        password = os.environ.get("LEXFLOW_ADMIN_PASSWORD", "")
        if not password:
            raise CommandError("Supply LEXFLOW_ADMIN_PASSWORD through the operator's secret environment.")
        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc
        office = Tenant.objects.create(slug=settings.LEXFLOW_TENANT_SLUG, name=opts["name"],
                                       security_profile=Tenant.SecurityProfile.PRIVATE,
                                       hosting_region=os.environ.get("LEXFLOW_HOSTING_REGION", "Dedicated environment — operator managed"))
        user = get_user_model().objects.create_user(opts["admin"], password=password)
        Membership.objects.create(tenant=office, user=user, role=Membership.Role.ADMIN)
        install_defaults(office)
        from apps.studio.models import MatterType
        MatterType.objects.filter(tenant=office).exclude(key="property-purchase").update(is_active=False)
        Connector.objects.create(tenant=office, name="Approved XML export", kind="xml", tier="import",
                                 destination="Vendor mapping pending validation")
        record(office, "security.office_provisioned", "Dedicated office provisioned without demo data", actor=user,
               object_type="tenant", object_ref=office.slug)
        self.stdout.write(self.style.SUCCESS("Office provisioned. Configure the destination and add named reviewer/approver accounts."))
