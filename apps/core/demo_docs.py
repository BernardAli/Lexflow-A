"""Synthetic German notarial documents rendered as real PDFs for demos and tests.

All persons, addresses and register sheets are fictitious.
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=10, leading=14)
SMALL = ParagraphStyle("small", parent=BASE, fontSize=8, leading=11, textColor=colors.HexColor("#555555"))
TITLE = ParagraphStyle("title", parent=BASE, fontName="Helvetica-Bold", fontSize=15, leading=20, spaceAfter=4)
LABEL = ParagraphStyle("label", parent=BASE, fontName="Helvetica-Bold")
H2 = ParagraphStyle("h2", parent=BASE, fontName="Helvetica-Bold", fontSize=11, leading=16, spaceBefore=8, spaceAfter=2)


class PdfDoc:
    def __init__(self, title, letterhead=None, subtitle=None):
        self.story = []
        if letterhead:
            self.story.append(Paragraph(letterhead, SMALL))
            self.story.append(Spacer(1, 6 * mm))
        self.story.append(Paragraph(title, TITLE))
        if subtitle:
            self.story.append(Paragraph(subtitle, SMALL))
        self.story.append(Spacer(1, 4 * mm))
        self.title = title

    def h(self, text):
        self.story.append(Paragraph(text, H2))
        return self

    def p(self, text):
        self.story.append(Paragraph(text, BASE))
        self.story.append(Spacer(1, 2 * mm))
        return self

    def rows(self, pairs):
        table = Table([[Paragraph(f"{k}:", LABEL), Paragraph(v, BASE)] for k, v in pairs], colWidths=[52 * mm, 110 * mm])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        self.story.append(table)
        self.story.append(Spacer(1, 3 * mm))
        return self

    def render(self) -> bytes:
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=20 * mm,
                                bottomMargin=20 * mm, title=self.title, author="LexFlow demo data")
        doc.build(self.story)
        return buf.getvalue()


def kaeuferfragebogen(p):
    return (
        PdfDoc("Käuferfragebogen", letterhead=f"{p['office']} · Mandanteninformation", subtitle="Angaben des Käufers zur Vorbereitung des Kaufvertrags")
        .h("1. Angaben zur Person")
        .rows([("Name", p["buyer"]), ("Geburtsdatum", p["buyer_birth"]), ("Anschrift", p["buyer_address"]),
               ("Telefon", p.get("phone", "089 1234567")), ("Familienstand", p.get("status", "ledig"))])
        .h("2. Angaben zum Kaufobjekt")
        .rows([("Verkäufer", p["seller"]), ("Objektanschrift", p["property_address"]),
               ("Grundbuch", p["grundbuch_q"]), ("Flurstück", p["parcel"]), ("Kaufpreis", p["price_q"]),
               ("Übergabetermin", p["handover"])])
        .h("3. Finanzierung")
        .p(p.get("finance", "Der Kaufpreis wird teilweise über ein Bankdarlehen finanziert. Eine Finanzierungsbestätigung wird nachgereicht."))
        .p("Ich versichere die Richtigkeit der vorstehenden Angaben.")
        .render()
    )


def grundbuchauszug(p):
    return (
        PdfDoc(f"Grundbuch von {p['gb_city']}", letterhead=f"Amtsgericht {p['gb_city']} · Grundbuchamt",
               subtitle="Amtlicher Ausdruck · Bestandsverzeichnis und Erste Abteilung")
        .h("Bestandsverzeichnis")
        .rows([("Grundbuchblatt", f"{p['gb_city']} Blatt {p['blatt_gb']}"), ("Gemarkung", p["gb_city"]),
               ("Flurstück", p["parcel"]), ("Lage", p["property_address"]),
               ("Wirtschaftsart", p.get("kind", "Gebäude- und Freifläche")), ("Größe", p.get("size", "412 m²"))])
        .h("Erste Abteilung — Eigentümer")
        .rows([("Eigentümer", p["seller"]), ("Grundlage der Eintragung", "Auflassung vom 14.05.2009, eingetragen am 02.07.2009")])
        .h("Zweite Abteilung")
        .p("Keine Eintragungen.")
        .h("Dritte Abteilung")
        .p(p.get("abt3", "Grundschuld ohne Brief zu 250.000,00 EUR für die Stadtsparkasse, eingetragen am 02.07.2009."))
        .render()
    )


def kaufvertragsentwurf(p):
    return (
        PdfDoc("Kaufvertrag — Entwurf", letterhead=f"{p['office']} · Urkundenrolle Nr. {p.get('ur', '1247')}/2026 (Entwurf)",
               subtitle="Entwurf zur Abstimmung mit den Beteiligten — nicht zur Unterschrift")
        .p("Vor mir, dem unterzeichnenden Notar, erschienen heute:")
        .rows([("Verkäufer", p["seller_full"]), ("Käufer", p["buyer_full"])])
        .h("§ 1 Grundbuchstand")
        .p(f"Im Grundbuch von {p['gb_city']} Blatt {p['blatt_contract']} ist der Verkäufer als Eigentümer des folgenden Grundbesitzes eingetragen:")
        .rows([("Grundbuch", f"{p['gb_city']} Blatt {p['blatt_contract']}"), ("Flurstück", p["parcel"]),
               ("Lage", p["property_address"])])
        .h("§ 2 Verkauf")
        .p("Der Verkäufer verkauft den vorbezeichneten Grundbesitz mit allen Rechten, Pflichten und dem gesetzlichen Zubehör an den Käufer.")
        .h("§ 3 Kaufpreis")
        .p(f"Der Kaufpreis beträgt {p['price_contract']} (in Worten: {p.get('price_words', 'achthundertfünfzigtausend Euro')}). "
           "Er ist fällig innerhalb von zehn Tagen nach Zugang der Fälligkeitsmitteilung des Notars.")
        .h("§ 4 Besitzübergang")
        .rows([("Besitzübergang", p["handover_contract"])])
        .p("Besitz, Nutzungen und Lasten gehen mit vollständiger Kaufpreiszahlung auf den Käufer über.")
        .render()
    )


def gruendungsfragebogen(p):
    return (
        PdfDoc("Gründungsfragebogen GmbH", letterhead=f"{p['office']} · Gesellschaftsrecht", subtitle="Angaben zur Gesellschaft")
        .rows([("Firma", p["company"]), ("Sitz der Gesellschaft", p["seat"]), ("Stammkapital", p["capital"]),
               ("Gegenstand des Unternehmens", p["purpose"])])
        .h("Geschäftsführung")
        .rows([("Geschäftsführer", p["director"]), ("Geburtsdatum", p["director_birth"]),
               ("Anschrift", p["director_address"]), ("Vertretung", "einzelvertretungsberechtigt, von § 181 BGB befreit")])
        .render()
    )


def hr_anmeldung(p):
    return (
        PdfDoc("Anmeldung zur Eintragung in das Handelsregister", letterhead=f"An das Amtsgericht {p['seat']} — Registergericht",
               subtitle="Neueintragung einer Gesellschaft mit beschränkter Haftung")
        .p(f"Zur Eintragung in das Handelsregister wird die Gesellschaft {p['company']} angemeldet.")
        .rows([("Firma", p["company"]), ("Sitz", p["seat"]), ("Stammkapital", p["capital_hr"]),
               ("Geschäftsführer", p["director"])])
        .p("Der Geschäftsführer versichert, dass die Einlagen auf die Geschäftsanteile vollständig geleistet sind.")
        .render()
    )


def vollmacht_fragebogen(p):
    return (
        PdfDoc("Fragebogen Vorsorgevollmacht", letterhead=f"{p['office']} · Vorsorge", subtitle="Angaben zur Vorbereitung der Vorsorgevollmacht")
        .rows([("Vollmachtgeber", p["principal"]), ("Geburtsdatum", p["principal_birth"]), ("Anschrift", p["principal_address"]),
               ("Bevollmächtigter", p["attorney"]), ("Umfang", p["scope"])])
        .p("Die Vollmacht soll im Zentralen Vorsorgeregister der Bundesnotarkammer registriert werden.")
        .render()
    )
