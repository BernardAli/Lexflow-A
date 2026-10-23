"""Shared fixtures for the test-suite."""
import shutil

from django.conf import settings
from django.contrib.auth import get_user_model

from apps.connectors.models import Connector
from apps.core import demo_docs as docs
from apps.core.models import Membership, Tenant
from apps.matters.services import ingest_message
from apps.studio.defaults import install_defaults

PDF = "application/pdf"
PASSWORD = "test-pass-2026"

PROPERTY = dict(
    office="Notariat Test", buyer="Anna Schmidt", buyer_birth="12.03.1985", buyer_address="Sonnenallee 24, 80331 München",
    seller="Peter Müller", property_address="Leopoldstraße 118, 80802 München", grundbuch_q="München Blatt 812",
    parcel="1234/5", price_q="850.000,00 EUR", handover="01.12.2026", gb_city="München", blatt_gb="812",
    blatt_contract="872", seller_full="Peter Müller, geb. 04.07.1958", buyer_full="Anna Schmidt, geb. 12.03.1985",
    price_contract="850.000,00 EUR", handover_contract="01.12.2026",
)


def reset_vault():
    shutil.rmtree(settings.LEXFLOW_VAULT_ROOT, ignore_errors=True)
    shutil.rmtree(settings.LEXFLOW_EXPORT_ROOT, ignore_errors=True)


def make_office(slug="alpha", name="Notariat Alpha"):
    tenant = Tenant.objects.create(slug=slug, name=name, city="München")
    install_defaults(tenant)
    Connector.objects.create(tenant=tenant, name="XML import", kind=Connector.Kind.XML)
    Connector.objects.create(tenant=tenant, name="Webhook", kind=Connector.Kind.JSON, signing_secret="s3cret")
    Connector.objects.create(tenant=tenant, name="CSV", kind=Connector.Kind.CSV)
    User = get_user_model()
    users = {}
    for role in ("clerk", "notary", "admin"):
        u = User.objects.create_user(f"{slug}.{role}", password=PASSWORD, first_name=role.title(), last_name=slug.title())
        Membership.objects.create(user=u, tenant=tenant, role=role)
        users[role] = u
    return tenant, users


def property_files(**overrides):
    p = {**PROPERTY, **overrides}
    return [
        ("Kaeuferfragebogen.pdf", PDF, docs.kaeuferfragebogen(p)),
        ("Grundbuchauszug.pdf", PDF, docs.grundbuchauszug(p)),
        ("Kaufvertragsentwurf.pdf", PDF, docs.kaufvertragsentwurf(p)),
    ]


def property_matter(tenant, **overrides):
    msg = ingest_message(tenant, subject="Unterlagen zum Kauf der Wohnung", body="Anbei die Unterlagen.",
                         sender_name="Anna Schmidt", files=property_files(**overrides))
    return msg.matter
