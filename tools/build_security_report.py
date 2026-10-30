#!/usr/bin/env python3
"""Render the reviewed EU security roadmap. This does not configure the application."""
import json
import math
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "presentation/security-roadmap.json"
OUTPUT = ROOT / "presentation/LexFlow_EU_Security_and_Compliance_Roadmap.pdf"
TEXT = OUTPUT.with_suffix(".txt")
W, H = A4
MARGIN = 46
WIDTH = W - 2 * MARGIN
INK = colors.HexColor("#163B37")
BODY = colors.HexColor("#273E3B")
MUTED = colors.HexColor("#5C706A")
PALE = colors.HexColor("#EDF3EF")
CREAM = colors.HexColor("#F7F5EF")
LINE = colors.HexColor("#D5E0D9")
GOLD = colors.HexColor("#A46A25")


def fonts():
    base = Path("/usr/share/fonts/truetype/lato")
    pdfmetrics.registerFont(TTFont("Lato", str(base / "Lato-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("LatoBold", str(base / "Lato-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("LatoItalic", str(base / "Lato-Italic.ttf")))
    pdfmetrics.registerFontFamily("Lato", normal="Lato", bold="LatoBold", italic="LatoItalic", boldItalic="LatoBold")
    pdfmetrics.registerFont(TTFont("Editorial", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"))


class Diagram(Flowable):
    """Vector trust-boundary diagram: all labelled safeguards are target design."""
    def __init__(self):
        Flowable.__init__(self)
        self.width, self.height = WIDTH, 287

    def draw(self):
        c = self.canv
        c.setFillColor(PALE)
        c.roundRect(0, 51, WIDTH, 183, 10, fill=1, stroke=0)
        c.setFillColor(MUTED)
        c.setFont("LatoBold", 8)
        c.drawString(14, 217, "DEDICATED OFFICE TRUST BOUNDARY · PROPOSED")

        def box(x, y, w, title, subtitle, dark=False):
            c.setFillColor(INK if dark else colors.white)
            c.setStrokeColor(LINE)
            c.roundRect(x, y, w, 49, 7, fill=1, stroke=not dark)
            c.setFillColor(colors.white if dark else INK)
            c.setFont("LatoBold", 10)
            c.drawCentredString(x + w / 2, y + 30, title)
            c.setFont("Lato", 8)
            c.drawCentredString(x + w / 2, y + 13, subtitle)

        def arrow(x1, y1, x2, y2):
            c.setStrokeColor(MUTED)
            c.setLineWidth(1)
            c.line(x1, y1, x2, y2)
            angle = math.atan2(y2 - y1, x2 - x1)
            for offset in (-.5, .5):
                c.line(x2, y2, x2 - 6 * math.cos(angle + offset), y2 - 6 * math.sin(angle + offset))

        box(0, 238, 178, "Managed staff devices", "Named identities · MFA")
        box(WIDTH - 178, 238, 178, "Approved destination", "ProNotar32: future adapter")
        box(14, 148, 143, "Private entry point", "TLS · access policy")
        box(180, 148, 143, "LexFlow review", "Evidence · approval · snapshot", True)
        box(346, 148, 143, "Controlled egress", "Approved exports · optional AI")
        box(14, 75, 143, "Office storage", "PostgreSQL · encrypted vault")
        box(180, 75, 143, "Key custody", "KMS/HSM · recovery separation")
        box(346, 75, 143, "Isolated parsing", "PDF / OCR · no network")
        box(0, 0, 238, "Independent recovery & audit custody", "Protected backups · latest audit anchors")
        box(WIDTH - 238, 0, 238, "Optional approved AI processor", "Contract · region · retention · transfer review")
        arrow(85, 238, 85, 197)
        arrow(157, 172, 180, 172)
        arrow(323, 172, 346, 172)
        arrow(198, 148, 125, 124)
        arrow(251, 148, 251, 124)
        arrow(310, 148, 359, 124)
        arrow(85, 75, 85, 49)
        c.setStrokeColor(MUTED)
        c.line(489, 172, WIDTH, 172)
        c.line(WIDTH, 172, WIDTH, 60)
        c.line(WIDTH, 60, 480, 60)
        arrow(480, 60, 480, 49)
        arrow(417, 197, 417, 238)


class CoverGraphic(Flowable):
    def __init__(self):
        Flowable.__init__(self)
        self.width, self.height = WIDTH, 147

    def draw(self):
        c = self.canv
        labels = [("01", "Protect", "Office data & identities"),
                  ("02", "Control", "AI & approved handoffs"),
                  ("03", "Prove", "Tests & operating evidence")]
        gap = 13
        width = (WIDTH - gap * 2) / 3
        for i, (number, title, caption) in enumerate(labels):
            x = i * (width + gap)
            c.setFillColor(INK if i == 1 else PALE)
            c.roundRect(x, 0, width, 140, 10, stroke=0, fill=1)
            c.setFillColor(colors.HexColor("#ADC8BB") if i == 1 else GOLD)
            c.setFont("LatoBold", 11)
            c.drawString(x + 17, 111, number)
            c.setFillColor(colors.white if i == 1 else INK)
            c.setFont("LatoBold", 23)
            c.drawString(x + 17, 65, title)
            c.setFont("Lato", 8.7)
            c.drawString(x + 17, 38, caption)


class Report(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name == "Title":
            key = "page-" + str(self.page)
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(flowable.getPlainText(), key, level=0)


def build():
    fonts()
    data = json.loads(SOURCE.read_text())
    styles = {
        "body": ParagraphStyle("Body", fontName="Lato", fontSize=10, leading=14.2, textColor=BODY, spaceAfter=8),
        "small": ParagraphStyle("Small", fontName="Lato", fontSize=8.4, leading=11.8, textColor=MUTED, spaceAfter=6),
        "title": ParagraphStyle("Title", fontName="Editorial", fontSize=27, leading=33, textColor=INK, spaceAfter=12),
        "deck": ParagraphStyle("Deck", fontName="Lato", fontSize=11.7, leading=16.5, textColor=MUTED, spaceAfter=14),
        "h2": ParagraphStyle("H2", fontName="LatoBold", fontSize=12, leading=16, textColor=INK, spaceBefore=6, spaceAfter=6, keepWithNext=True),
        "ref": ParagraphStyle("Reference", fontName="LatoBold", fontSize=10.2, leading=13.2, textColor=INK, spaceBefore=5, spaceAfter=4, keepWithNext=True),
        "eyebrow": ParagraphStyle("Eyebrow", fontName="LatoBold", fontSize=8.4, leading=12, textColor=GOLD, spaceAfter=9),
        "cell": ParagraphStyle("Cell", fontName="Lato", fontSize=9.1, leading=12.6, textColor=BODY),
        "th": ParagraphStyle("TH", fontName="LatoBold", fontSize=8.5, leading=11.5, textColor=colors.white),
    }
    refs = {ref["id"]: ref for ref in data["sources"]}
    story = []

    def p(value, style="body"):
        return Paragraph(value, styles[style])

    def links(ids):
        return " · ".join(f'<link href={quoteattr(refs[key]["url"])} color="#235D4A">[{key}] {escape(refs[key]["short"])}</link>' for key in ids)

    def table(headers, rows, proportions):
        grid = Table([[p(escape(x), "th") for x in headers]] + [[p(x, "cell") for x in row] for row in rows],
                     colWidths=[WIDTH * x for x in proportions], repeatRows=1, hAlign="LEFT")
        grid.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), INK),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("LINEBELOW", (0, 0), (-1, -1), .4, LINE),
        ]))
        return grid

    def box(title, text):
        t = Table([[p(title, "h2")], [p(text)]], colWidths=[WIDTH])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PALE),
            ("LINEBEFORE", (0, 0), (0, -1), 3, GOLD),
            ("LEFTPADDING", (0, 0), (-1, -1), 13),
            ("RIGHTPADDING", (0, 0), (-1, -1), 13),
            ("TOPPADDING", (0, 0), (-1, 0), 9),
            ("BOTTOMPADDING", (0, -1), (-1, -1), 9),
        ]))
        return t

    story.extend([Spacer(1, 35), p("LEXFLOW / EU ASSURANCE ROADMAP", "eyebrow"), Spacer(1, 15)])
    cover_title = ParagraphStyle("Cover", parent=styles["title"], fontSize=40, leading=47, spaceAfter=20)
    story.append(Paragraph("Built for trust.<br/>Ready to prove it.", cover_title))
    story.append(p("A practical route to strong EU safety,<br/>security and data-protection assurance<br/>for German notarial workflows.", "deck"))
    story.append(Spacer(1, 18))
    story.append(CoverGraphic())
    story.append(Spacer(1, 22))
    story.append(box("Implementation roadmap · not a compliance certificate", "LexFlow has useful pilot safeguards. Production assurance still requires legal scoping, additional controls, validated deployment and independent evidence. This report defines that work and the gates for acceptance."))
    story.append(Spacer(1, 18))
    story.append(p(f'{data["date"]} · Version {data["version"]} · Baseline {data["baseline"]}', "small"))
    story.append(p("Prepared for the project team, office leadership, security reviewers and privacy advisers. No application behavior or production configuration is changed by this document.", "small"))

    for index, page in enumerate(data["pages"], start=2):
        story.append(PageBreak())
        story.append(p(f'{index - 1:02d} / {escape(page["label"].upper())}', "eyebrow"))
        story.append(p(page["title"], "title"))
        if page.get("deck"):
            story.append(p(page["deck"], "deck"))
        for block in page["blocks"]:
            kind = block["type"]
            if kind in ("p", "small", "h2"):
                story.append(p(block["text"], {"p": "body"}.get(kind, kind)))
            elif kind == "table":
                story.append(table(block["headers"], block["rows"], block["widths"]))
                story.append(Spacer(1, 10))
            elif kind == "box":
                story.append(box(block["title"], block["text"]))
                story.append(Spacer(1, 10))
            elif kind == "diagram":
                story.append(Diagram())
                story.append(Spacer(1, 12))
            elif kind == "sources":
                for key in block["ids"]:
                    ref = refs[key]
                    story.append(p(f'[{key}] {escape(ref["title"])}', "ref"))
                    story.append(p(escape(ref["note"]), "small"))
                    story.append(p(f'<link href={quoteattr(ref["url"])} color="#235D4A">{escape(ref["url"])}</link>', "small"))
        if page.get("refs"):
            story.append(Spacer(1, 5))
            story.append(p("Sources: " + links(page["refs"]), "small"))

    count = len(data["pages"]) + 1

    def page_art(c, doc):
        c.saveState()
        c.setTitle("LexFlow — EU Security and Compliance Roadmap")
        c.setAuthor("LexFlow project documentation")
        c.setSubject("Prospective control roadmap; pilot evidence and gaps; regulatory review dated 30 October 2026")
        c.setKeywords("LexFlow, GDPR, AI Act, CRA, NIS2, notarial confidentiality, ProNotar32")
        c.setFillColor(CREAM if doc.page == 1 else colors.white)
        c.rect(0, 0, W, H, fill=1, stroke=0)
        if doc.page != 1:
            c.setFillColor(INK)
            c.setFont("LatoBold", 11)
            c.drawString(MARGIN, H - 32, "LEXFLOW")
            c.setFillColor(MUTED)
            c.setFont("Lato", 8)
            c.drawRightString(W - MARGIN, H - 32, "EU SECURITY & COMPLIANCE / IMPLEMENTATION ROADMAP")
            c.setStrokeColor(LINE)
            c.line(MARGIN, H - 43, W - MARGIN, H - 43)
        c.setStrokeColor(LINE)
        c.line(MARGIN, 43, W - MARGIN, 43)
        c.setFillColor(MUTED)
        c.setFont("Lato", 7.7)
        c.drawString(MARGIN, 28, f'{data["date"]}  ·  v{data["version"]}  ·  Planning document / pilot baseline')
        c.setFont("LatoBold", 8)
        c.drawRightString(W - MARGIN, 28, f"{doc.page:02d} / {count:02d}")
        c.restoreState()

    doc = Report(str(OUTPUT), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                 topMargin=65, bottomMargin=58, pageCompression=1)
    doc.build(story, onFirstPage=page_art, onLaterPages=page_art)
    reader = PdfReader(OUTPUT)
    if len(reader.pages) != count:
        raise RuntimeError(f"Unexpected page overflow: expected {count}, got {len(reader.pages)}")
    text = "\n\n".join(f"PAGE {i}\n{page.extract_text()}" for i, page in enumerate(reader.pages, 1))
    TEXT.write_text(text)
    print(f"Created {OUTPUT.relative_to(ROOT)} ({count} pages, {OUTPUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    build()
