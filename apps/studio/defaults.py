"""Starter workflows installed for every new office. Offices extend them in the studio."""
from decimal import Decimal

from django.db import transaction

from .models import DocumentType, FieldDefinition, MatterType

MATTER_TYPES = [
    {
        "key": "property-purchase",
        "name": "Property purchase",
        "german_name": "Grundstückskaufvertrag",
        "description": "Purchase of real estate: parties, price and land register sheet.",
        "subject_keywords": ["Kauf", "Kaufvertrag", "Immobilie", "Grundstück", "Wohnung", "purchase", "property"],
        "fields": [
            ("buyer_name", "Buyer", "name", "Beteiligter.Kaeufer.Name",
             ["Name des Käufers", "Käufer", "Erwerber", "Name"], True, False, "0.90"),
            ("buyer_birthdate", "Buyer date of birth", "date", "Beteiligter.Kaeufer.Geburtsdatum",
             ["Geburtsdatum", "geboren am"], True, False, "0.90"),
            ("buyer_address", "Buyer address", "address", "Beteiligter.Kaeufer.Anschrift",
             ["Anschrift", "Wohnanschrift"], True, False, "0.90"),
            ("seller_name", "Seller", "name", "Beteiligter.Verkaeufer.Name",
             ["Verkäufer", "Eigentümer", "Veräußerer"], True, False, "0.90"),
            ("purchase_price", "Purchase price", "money", "Vorgang.Kaufpreis",
             ["Kaufpreis", "Gesamtkaufpreis"], True, True, "0.95"),
            ("grundbuch", "Land register sheet", "grundbuch", "Grundbuch.Bezeichnung",
             ["Grundbuch", "Grundbuchblatt"], True, False, "0.90"),
            ("parcel", "Parcel", "parcel", "Grundbuch.Flurstueck", ["Flurstück", "Flst."], False, False, "0.90"),
            ("property_address", "Property address", "address", "Objekt.Anschrift",
             ["Lage", "Objekt", "Objektanschrift", "Grundstück"], True, False, "0.90"),
            ("handover_date", "Handover date", "date", "Vorgang.Besitzuebergang",
             ["Besitzübergang", "Übergabe", "Übergabetermin"], False, False, "0.85"),
        ],
        "documents": [
            ("kaeuferfragebogen", "Buyer questionnaire (Käuferfragebogen)",
             ["Käuferfragebogen", "Angaben des Käufers", "Fragebogen"], "1.20"),
            ("grundbuchauszug", "Land register extract (Grundbuchauszug)",
             ["Grundbuch von", "Bestandsverzeichnis", "Erste Abteilung", "Amtsgericht", "Abteilung I"], "1.20"),
            ("kaufvertragsentwurf", "Draft purchase contract (Kaufvertragsentwurf)",
             ["Kaufvertrag", "Entwurf", "verkauft", "Urkundenrolle"], "1.00"),
        ],
    },
    {
        "key": "gmbh-formation",
        "name": "GmbH formation",
        "german_name": "GmbH-Gründung",
        "description": "New limited company: name, seat, capital and managing director.",
        "subject_keywords": ["GmbH", "Gründung", "Gesellschaft", "Handelsregister", "formation"],
        "fields": [
            ("company_name", "Company name", "name", "Gesellschaft.Firma", ["Firma", "Firmenname"], True, False, "0.90"),
            ("seat", "Registered seat", "text", "Gesellschaft.Sitz", ["Sitz der Gesellschaft", "Sitz"], True, False, "0.90"),
            ("share_capital", "Share capital", "money", "Gesellschaft.Stammkapital", ["Stammkapital"], True, True, "0.95"),
            ("managing_director", "Managing director", "name", "Gesellschaft.Geschaeftsfuehrer.Name",
             ["Geschäftsführer", "Geschäftsführerin"], True, False, "0.90"),
            ("director_birthdate", "Director date of birth", "date", "Gesellschaft.Geschaeftsfuehrer.Geburtsdatum",
             ["Geburtsdatum", "geboren am"], True, False, "0.90"),
            ("business_purpose", "Business purpose", "text", "Gesellschaft.Gegenstand",
             ["Gegenstand des Unternehmens", "Unternehmensgegenstand"], False, False, "0.85"),
        ],
        "documents": [
            ("hr-anmeldung", "Commercial register filing (Handelsregisteranmeldung)",
             ["Handelsregister", "Anmeldung", "zur Eintragung"], "1.20"),
            ("gesellschafterliste", "List of shareholders (Gesellschafterliste)",
             ["Gesellschafterliste", "Geschäftsanteil", "Nennbetrag"], "1.10"),
            ("gruendungsfragebogen", "Formation questionnaire", ["Gründungsfragebogen", "Angaben zur Gesellschaft"], "1.20"),
        ],
    },
    {
        "key": "power-of-attorney",
        "name": "Lasting power of attorney",
        "german_name": "Vorsorgevollmacht",
        "description": "Principal, attorney and scope of a lasting power of attorney.",
        "subject_keywords": ["Vorsorgevollmacht", "Vollmacht", "Patientenverfügung", "power of attorney"],
        "fields": [
            ("principal_name", "Principal", "name", "Vollmacht.Vollmachtgeber.Name", ["Vollmachtgeber", "Vollmachtgeberin"], True, False, "0.90"),
            ("principal_birthdate", "Principal date of birth", "date", "Vollmacht.Vollmachtgeber.Geburtsdatum", ["Geburtsdatum", "geboren am"], True, False, "0.90"),
            ("principal_address", "Principal address", "address", "Vollmacht.Vollmachtgeber.Anschrift", ["Anschrift", "wohnhaft"], True, False, "0.90"),
            ("attorney_name", "Attorney", "name", "Vollmacht.Bevollmaechtigter.Name", ["Bevollmächtigter", "Bevollmächtigte"], True, False, "0.90"),
            ("scope", "Scope", "text", "Vollmacht.Umfang", ["Umfang", "Umfang der Vollmacht"], False, True, "0.90"),
        ],
        "documents": [
            ("vollmacht-fragebogen", "Power of attorney questionnaire",
             ["Vorsorgevollmacht", "Bevollmächtigte", "Vollmachtgeber"], "1.20"),
        ],
    },
]


@transaction.atomic
def install_defaults(tenant):
    for order, spec in enumerate(MATTER_TYPES):
        mt, _ = MatterType.objects.update_or_create(
            tenant=tenant, key=spec["key"],
            defaults={"name": spec["name"], "german_name": spec["german_name"], "description": spec["description"],
                      "subject_keywords": spec["subject_keywords"], "sort_order": order},
        )
        for i, (key, label, dtype, dest, anchors, required, always, threshold) in enumerate(spec["fields"]):
            existing = FieldDefinition.objects.filter(matter_type=mt, key=key).first()
            learned = [a for a in (existing.anchors if existing else []) if a not in anchors]
            FieldDefinition.objects.update_or_create(
                matter_type=mt, key=key,
                defaults={"label": label, "data_type": dtype, "destination_path": dest, "anchors": anchors + learned,
                          "required": required, "always_review": always, "auto_threshold": Decimal(threshold),
                          "sort_order": i},
            )
        for key, name, keywords, weight in spec["documents"]:
            DocumentType.objects.update_or_create(
                tenant=tenant, key=key,
                defaults={"name": name, "keywords": keywords, "matter_type": mt, "weight": Decimal(weight)},
            )
