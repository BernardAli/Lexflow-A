"""Idempotent demo data. Every matter is created by running real PDFs through the real pipeline."""
import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.connectors.models import Connector
from apps.connectors.services import acknowledge, transfer
from apps.core import demo_docs as docs
from apps.core.models import Membership, Tenant
from apps.intake.models import IntakeMessage
from apps.matters import services
from apps.matters.models import ExtractedField, Matter
from apps.studio.defaults import install_defaults

PASSWORD = "lexflow-demo-2026"
PDF = "application/pdf"

FIRST = ["Anna", "Lukas", "Sophie", "Jonas", "Lea", "Felix", "Marie", "Paul", "Hannah", "Elias", "Emilia", "Noah",
         "Mia", "Leon", "Clara", "Ben", "Ayşe", "Mateusz", "Ida", "Karl"]
LAST = ["Schmidt", "Weber", "Fischer", "Becker", "Wagner", "Schulz", "Koch", "Richter", "Klein", "Wolf", "Neumann",
        "Schwarz", "Zimmermann", "Braun", "Krüger", "Hartmann", "Yilmaz", "Kowalski", "Lange", "Vogel"]
STREETS = ["Leopoldstraße", "Sonnenallee", "Theresienstraße", "Nymphenburger Straße", "Belgradstraße",
           "Kurfürstenstraße", "Isartalstraße", "Barer Straße", "Kaiserstraße", "Rosenheimer Straße"]
CITIES = [("München", "80"), ("Freising", "85"), ("Augsburg", "86"), ("Dachau", "85")]


class Command(BaseCommand):
    help = "Create demo notary offices, users, workflows, connectors and processed matters."

    def add_arguments(self, parser):
        parser.add_argument("--single-office", action="store_true", help="Seed only Keller for the dedicated presentation demo")

    def handle(self, *args, **opts):
        if not settings.LEXFLOW_DEMO_MODE:
            raise CommandError("Demo seeding is disabled. Provision a real office with provision_office.")
        self.single_office = opts.get("single_office") or settings.LEXFLOW_DEPLOYMENT == "dedicated"
        if self.single_office and Tenant.objects.exclude(slug="keller").exists():
            raise CommandError("Use a fresh isolated presentation database for a single-office demo.")
        self.rng = random.Random(2026)
        User = get_user_model()
        with transaction.atomic():
            keller, _ = Tenant.objects.update_or_create(
                slug="keller", defaults={"name": "Notariat Dr. Keller", "city": "München",
                                         "security_profile": Tenant.SecurityProfile.PRIVATE,
                                         "hosting_region": "Local synthetic presentation environment"})
            hoffmann = None
            if not self.single_office:
                hoffmann, _ = Tenant.objects.update_or_create(
                slug="hoffmann", defaults={"name": "Notariat Hoffmann & Partner", "city": "Hamburg",
                                           "security_profile": Tenant.SecurityProfile.EU_SAAS})
            people = [
                ("admin", "Office", "Administrator", [(keller, "admin", "Office administrator")] +
                 ([(hoffmann, "admin", "Office administrator")] if hoffmann else []), True),
                ("keller.notar", "Thomas", "Keller", [(keller, "notary", "Notar")], False),
                ("keller.clerk", "Julia", "Brandt", [(keller, "clerk", "Notarfachangestellte")], False),
                ("hoffmann.notar", "Sabine", "Hoffmann", [(hoffmann, "notary", "Notarin")], False),
                ("hoffmann.clerk", "Mehmet", "Yilmaz", [(hoffmann, "clerk", "Notarfachangestellter")], False),
            ]
            people = [entry for entry in people if all(tenant is not None for tenant, _, _ in entry[3])]
            for username, first, last, memberships, superuser in people:
                user, created = User.objects.update_or_create(
                    username=username, defaults={"first_name": first, "last_name": last,
                                                 "email": f"{username}@example.test", "is_staff": superuser,
                                                 "is_superuser": superuser})
                if created or not user.has_usable_password():
                    user.set_password(PASSWORD)
                    user.save()
                for tenant, role, title in memberships:
                    Membership.objects.update_or_create(user=user, tenant=tenant, defaults={"role": role, "title": title})
            for t in [office for office in (keller, hoffmann) if office is not None]:
                install_defaults(t)
                self._connectors(t)
                if not t.api_token_hash:
                    token = t.issue_api_token()
                    self.stdout.write(f"  API token for {t.name}: {token}")

        self.keller_notary = User.objects.get(username="keller.notar")
        self.keller_clerk = User.objects.get(username="keller.clerk")
        self.hoffmann_notary = User.objects.filter(username="hoffmann.notar").first()
        self._keller_inbox(keller)
        self._history(keller, self.keller_clerk, self.keller_notary, 30, "keller-hist")
        if hoffmann:
            self._history(hoffmann, User.objects.get(username="hoffmann.clerk"), self.hoffmann_notary, 4, "hoffmann-hist",
                          office="Notariat Hoffmann & Partner, Hamburg", cities=[("Hamburg", "22")])
        self.stdout.write(self.style.SUCCESS(
            f"Seeded {Matter.objects.count()} matters across {Tenant.objects.count()} offices. Password for all users: {PASSWORD}"
        ))

    # ------------------------------------------------------------------ setup
    def _connectors(self, tenant):
        specs = [
            ("Notary software import", Connector.Kind.XML, Connector.Tier.IMPORT,
             "Generic pilot XML export; vendor mapping is agreed before live use.", "Pilot import folder / manual import", ""),
            ("Case-management webhook", Connector.Kind.JSON, Connector.Tier.IMPORT,
             "HMAC-signed JSON file; no network delivery is claimed.", "Approved JSON export", f"whsec-{tenant.slug}-demo"),
            ("Spreadsheet export", Connector.Kind.CSV, Connector.Tier.IMPORT,
             "Semicolon CSV for offices without an import interface yet.", "Excel / manual import", ""),
        ]
        for name, kind, tier, desc, dest, secret in specs:
            Connector.objects.update_or_create(
                tenant=tenant, name=name,
                defaults={"kind": kind, "tier": tier, "description": desc, "destination": dest, "signing_secret": secret,
                          "delivery": Connector.Delivery.FOLDER if kind == Connector.Kind.XML else Connector.Delivery.DOWNLOAD})

    def _ingest(self, tenant, external_id, **kwargs):
        existing = IntakeMessage.objects.filter(tenant=tenant, external_id=external_id).first()
        if existing:
            return existing, False
        return services.ingest_message(tenant, external_id=external_id, is_demo=True, **kwargs), True

    def _backdate(self, message, minutes_ago, review_minutes=None):
        received = timezone.now() - timedelta(minutes=minutes_ago)
        IntakeMessage.objects.filter(pk=message.pk).update(received_at=received, processed_at=received + timedelta(seconds=9))
        updates = {"created_at": received}
        if review_minutes is not None:
            updates["approved_at"] = received + timedelta(minutes=review_minutes)
            if Matter.objects.filter(message=message, status=Matter.Status.TRANSFERRED).exists():
                updates["transferred_at"] = received + timedelta(minutes=review_minutes, seconds=40)
        Matter.objects.filter(message=message).update(**updates)

    # --------------------------------------------------------- current inbox
    def _keller_inbox(self, t):
        office = "Notariat Dr. Keller, München"
        p = dict(
            office=office, buyer="Anna Schmidt", buyer_birth="12.03.1985", buyer_address="Sonnenallee 24, 80331 München",
            seller="Peter Müller", property_address="Leopoldstraße 118, 80802 München", grundbuch_q="München Blatt 812",
            parcel="1234/5", price_q="850.000,00 EUR", handover="01.12.2026", gb_city="München", blatt_gb="812",
            blatt_contract="872", seller_full="Peter Müller, geb. 04.07.1958, wohnhaft Ungererstraße 40, 80805 München",
            buyer_full="Anna Schmidt, geb. 12.03.1985, wohnhaft Sonnenallee 24, 80331 München",
            price_contract="850.000,00 EUR", handover_contract="01.12.2026",
        )
        msg, new = self._ingest(
            t, "demo-schmidt-purchase", sender_name="Anna Schmidt", sender_email="anna.schmidt@example.test",
            subject="Unterlagen zum Kauf der Wohnung Leopoldstraße 118",
            body=("Sehr geehrter Herr Dr. Keller,\n\nanbei erhalten Sie den Käuferfragebogen, den Grundbuchauszug und den "
                  "Kaufvertragsentwurf für den geplanten Kauf.\n\nMit freundlichen Grüßen\nAnna Schmidt"),
            files=[("Kaeuferfragebogen.pdf", PDF, docs.kaeuferfragebogen(p)),
                   ("Grundbuchauszug.pdf", PDF, docs.grundbuchauszug(p)),
                   ("Kaufvertragsentwurf.pdf", PDF, docs.kaufvertragsentwurf(p))],
        )
        if new:
            self._backdate(msg, 26)

        g = dict(office=office, company="Isarwerk Software GmbH", seat="München", capital="25.000,00 EUR",
                 capital_hr="25.000,00 EUR", purpose="Entwicklung und Vertrieb von Software sowie damit verbundene Dienstleistungen",
                 director="Lukas Hartmann", director_birth="22.09.1990", director_address="Barer Straße 51, 80799 München")
        msg, new = self._ingest(
            t, "demo-isarwerk-gmbh", sender_name="Lukas Hartmann", sender_email="l.hartmann@example.test",
            subject="Gründung Isarwerk Software GmbH — Unterlagen",
            body="Guten Tag,\n\nwie besprochen sende ich die Angaben zur Gründung unserer GmbH.\n\nViele Grüße\nLukas Hartmann",
            files=[("Gruendungsfragebogen.pdf", PDF, docs.gruendungsfragebogen(g)),
                   ("HR-Anmeldung_Entwurf.pdf", PDF, docs.hr_anmeldung(g))],
        )
        if new:
            self._backdate(msg, 74)

        v = dict(office=office, principal="Helga Weber", principal_birth="03.11.1947",
                 principal_address="Isartalstraße 9, 80469 München", attorney="Markus Weber",
                 scope="Vermögenssorge, Gesundheitssorge und Aufenthaltsbestimmung")
        msg, new = self._ingest(
            t, "demo-weber-vollmacht", source=IntakeMessage.Source.UPLOAD, sender_name="Markus Weber",
            sender_email="markus.weber@example.test", subject="Vorsorgevollmacht für meine Mutter",
            body="", files=[("Fragebogen_Vorsorgevollmacht.pdf", PDF, docs.vollmacht_fragebogen(v))],
        )
        if new:
            self._backdate(msg, 140)

        f = dict(office=office, buyer="Jonas Becker", buyer_birth="08.06.1988", buyer_address="Kaiserstraße 12, 85354 Freising",
                 seller="Clara Neumann", property_address="Vöttinger Straße 31, 85354 Freising", grundbuch_q="Freising Blatt 2290",
                 parcel="877/3", price_q="612.500,00 EUR", handover="15.01.2027", gb_city="Freising", blatt_gb="2290",
                 blatt_contract="2290", seller_full="Clara Neumann, geb. 19.02.1961, wohnhaft Am Lindenkeller 4, 85354 Freising",
                 buyer_full="Jonas Becker, geb. 08.06.1988, wohnhaft Kaiserstraße 12, 85354 Freising",
                 price_contract="612.500,00 EUR", price_words="sechshundertzwölftausendfünfhundert Euro",
                 handover_contract="15.01.2027")
        msg, new = self._ingest(
            t, "demo-becker-freising", sender_name="Jonas Becker", sender_email="jonas.becker@example.test",
            subject="Kaufvertrag Doppelhaushälfte Freising", process=False,
            body="Sehr geehrte Damen und Herren,\n\nanbei die Unterlagen für den Kauf in Freising.\n\nBeste Grüße\nJonas Becker",
            files=[("Kaeuferfragebogen_Becker.pdf", PDF, docs.kaeuferfragebogen(f)),
                   ("Grundbuchauszug_Freising.pdf", PDF, docs.grundbuchauszug(f)),
                   ("Kaufvertragsentwurf_Freising.pdf", PDF, docs.kaufvertragsentwurf(f))],
        )
        if new:
            IntakeMessage.objects.filter(pk=msg.pk).update(received_at=timezone.now() - timedelta(minutes=4))

        msg, new = self._ingest(
            t, "demo-scan-0042", source=IntakeMessage.Source.FOLDER, sender_name="Scanner Empfang",
            subject="Scan_0042 vom Kopierer Empfang", body="",
            files=[("Scan_0042.txt", "text/plain",
                    "Notiz Telefonat: Rückruf erbeten wegen Termin. Unterlagen folgen per Post.".encode())],
        )
        if new:
            self._backdate(msg, 190)

    # --------------------------------------------------------------- history
    def _history(self, t, clerk, notary, count, prefix, office="Notariat Dr. Keller, München", cities=None):
        cities = cities or CITIES
        rng = self.rng
        membership = Membership.objects.get(user=notary, tenant=t)
        xml = Connector.objects.get(tenant=t, name="Notary software import", kind=Connector.Kind.XML)
        webhook = Connector.objects.get(tenant=t, name="Case-management webhook", kind=Connector.Kind.JSON)
        for i in range(count):
            ext = f"{prefix}-{i:02d}"
            if IntakeMessage.objects.filter(tenant=t, external_id=ext).exists():
                rng.random()
                continue
            city, plz = rng.choice(cities)
            buyer = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            seller = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            blatt = str(rng.randint(300, 9800))
            conflict = rng.random() < 0.22
            price = rng.randrange(280, 1450) * 1000
            price_de = f"{price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " EUR"
            addr_b = f"{rng.choice(STREETS)} {rng.randint(1, 140)}, {plz}{rng.randint(100, 999)} {city}"
            addr_p = f"{rng.choice(STREETS)} {rng.randint(1, 140)}, {plz}{rng.randint(100, 999)} {city}"
            p = dict(office=office, buyer=buyer, buyer_birth=f"{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.{rng.randint(1955, 1999)}",
                     buyer_address=addr_b, seller=seller, property_address=addr_p, grundbuch_q=f"{city} Blatt {blatt}",
                     parcel=f"{rng.randint(10, 3999)}/{rng.randint(1, 9)}", price_q=price_de, handover="01.02.2027",
                     gb_city=city, blatt_gb=blatt, blatt_contract=str(int(blatt) + 60) if conflict else blatt,
                     seller_full=f"{seller}, geb. 11.04.1962", buyer_full=f"{buyer}, geb. 02.05.1984",
                     price_contract=price_de, price_words="—", handover_contract="01.02.2027")
            files = [(f"Kaeuferfragebogen_{buyer.split()[-1]}.pdf", PDF, docs.kaeuferfragebogen(p)),
                     ("Grundbuchauszug.pdf", PDF, docs.grundbuchauszug(p))]
            if rng.random() < 0.8:
                files.append(("Kaufvertragsentwurf.pdf", PDF, docs.kaufvertragsentwurf(p)))
            msg = services.ingest_message(
                t, external_id=ext, sender_name=buyer, sender_email=f"{buyer.split()[0].lower()}@example.test",
                subject=f"Kauf {addr_p.split(',')[0]} — Unterlagen", body="Anbei die Unterlagen.\n\nMit freundlichen Grüßen",
                files=files, is_demo=True)
            matter = msg.matter
            for field in matter.fields.select_related("definition"):
                if field.status == ExtractedField.Status.CONFLICT:
                    winner = field.candidates.filter(source__doc_type__key="grundbuchauszug").first() or field.candidates.first()
                    services.choose_candidate(field, clerk, winner)
                elif field.status == ExtractedField.Status.MISSING:
                    services.correct_field(field, clerk, "01.02.2027" if field.definition.data_type == "date" else "Nachgereicht")
                elif field.status == ExtractedField.Status.REVIEW:
                    services.confirm_field(field, clerk)
                elif field.status == ExtractedField.Status.AUTO and rng.random() < 0.015:
                    services.correct_field(field, clerk, field.normalized or field.value)
            matter.refresh_from_db()
            if not services.approval_blockers(matter):
                services.approve(matter, membership)
                if rng.random() < 0.85:
                    exported = transfer(matter, xml if rng.random() < 0.7 else webhook, notary)
                    acknowledge(exported, notary, checksum=exported.checksum, receipt_reference="SYNTHETIC-" + exported.ref)
            days_ago = max(1, round((count - i) * 13 / count))
            self._backdate(msg, days_ago * 24 * 60 + rng.randint(60, 480), review_minutes=rng.randint(4, 13))
