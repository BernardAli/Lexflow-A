#!/usr/bin/env python3
"""Build synthetic upload PDFs and a printable LexFlow demonstration guide."""
import argparse
import io
import json
from pathlib import Path
import sys
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core import demo_docs

OUT = ROOT / "presentation"
DOCUMENTS = OUT / "demo-documents"
GUIDE = OUT / "LexFlow_Demo_Guide.pdf"
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
INK = colors.HexColor("#142D2C")
GREEN = colors.HexColor("#1E5250")
PALE = colors.HexColor("#F0F5F3")
MUTED = colors.HexColor("#526761")
DATE = "30 October 2026"

PROPERTY = {
    "office": "Notariat Dr. Keller (synthetic demonstration)",
    "buyer": "Anna Schmidt", "buyer_birth": "12.03.1985",
    "buyer_address": "Sonnenallee 24, 80331 München", "seller": "Peter Müller",
    "property_address": "Leopoldstraße 118, 80802 München", "grundbuch_q": "München Blatt 812",
    "parcel": "1234/5", "price_q": "850.000,00 EUR", "handover": "01.12.2026",
    "gb_city": "München", "blatt_gb": "812", "blatt_contract": "872",
    "seller_full": "Peter Müller, geb. 04.07.1958, wohnhaft Ungererstraße 40, 80805 München",
    "buyer_full": "Anna Schmidt, geb. 12.03.1985, wohnhaft Sonnenallee 24, 80331 München",
    "price_contract": "850.000,00 EUR", "handover_contract": "01.12.2026",
}


def label_synthetic(data):
    """Keep the source layout while visibly identifying every page as a sample."""
    reader, writer = PdfReader(io.BytesIO(data)), PdfWriter()
    for page in reader.pages:
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        overlay = io.BytesIO()
        drawing = canvas.Canvas(overlay, pagesize=(width, height))
        drawing.setFillColor(GREEN)
        drawing.setFont("Helvetica-Bold", 8)
        drawing.drawString(62, height - 28, "LEXFLOW / SYNTHETIC DEMONSTRATION DOCUMENT")
        drawing.setFillColor(MUTED)
        drawing.setFont("Helvetica", 8)
        drawing.drawString(62, 29, "Fictional persons, addresses and register entries. Sample only; not an official record.")
        drawing.save()
        page.merge_page(PdfReader(io.BytesIO(overlay.getvalue())).pages[0])
        writer.add_page(page)
    writer.add_metadata({"/Title": reader.metadata.title or "LexFlow synthetic sample", "/Author": "LexFlow demo data"})
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def build_sources():
    groups = {}
    for folder, contract_sheet in (("property-conflict", "872"), ("property-matching", "812")):
        data = {**PROPERTY, "blatt_contract": contract_sheet}
        paths = []
        for filename, render in (
            ("01_Kaeuferfragebogen.pdf", demo_docs.kaeuferfragebogen),
            ("02_Grundbuchauszug.pdf", demo_docs.grundbuchauszug),
            ("03_Kaufvertragsentwurf.pdf", demo_docs.kaufvertragsentwurf),
        ):
            path = DOCUMENTS / folder / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(label_synthetic(render(data)))
            paths.append(path)
        groups[folder] = paths

    # Save image pixels as a PDF: there is deliberately no searchable text layer.
    lines = [
        "LEXFLOW / SYNTHETIC DEMONSTRATION DOCUMENT", "", "Käuferfragebogen",
        "Käufer: Maria Beispiel", "Geburtsdatum: 21.08.1987",
        "Anschrift: Lindenweg 12, 10115 Berlin", "Verkäufer: Thomas Muster",
        "Kaufpreis: 425.500,00 EUR", "Grundbuch: Berlin Blatt 421", "Flurstück: 123/4",
        "Objektanschrift: Parkstraße 9, 10115 Berlin", "Übergabetermin: 15.02.2027", "",
        "Fiktive Daten. Nur zur Demonstration. Kein amtliches Dokument.",
    ]
    image = Image.new("RGB", (1800, 1400), "white")
    drawing = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT_DIR / "DejaVuSans.ttf"), 34)
    for index, line in enumerate(lines):
        drawing.text((65, 70 + index * 70), line, fill="black", font=font)
    scan = DOCUMENTS / "scan-review" / "01_Gescannter_Kaeuferfragebogen.pdf"
    scan.parent.mkdir(parents=True, exist_ok=True)
    image.save(scan, "PDF", resolution=160, title="Synthetic scanned buyer questionnaire", author="LexFlow demo data")
    groups["scan-review"] = [scan]
    return groups


def build_guide():
    pdfmetrics.registerFont(TTFont("Demo", str(FONT_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DemoBold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
    body = ParagraphStyle("Body", fontName="Demo", fontSize=9.2, leading=14, textColor=INK, spaceAfter=7)
    small = ParagraphStyle("Small", parent=body, fontSize=8, leading=11, textColor=MUTED, spaceAfter=4)
    title = ParagraphStyle("Title", parent=body, fontName="DemoBold", fontSize=26, leading=32, spaceAfter=12)
    h2 = ParagraphStyle("Heading", parent=body, fontName="DemoBold", fontSize=13, leading=18, textColor=GREEN, spaceBefore=10, spaceAfter=6)
    cell = ParagraphStyle("Cell", parent=body, fontSize=8.5, leading=12, spaceAfter=0, alignment=TA_LEFT)
    story = []

    def p(text, style=body):
        story.append(Paragraph(text, style))

    def heading(text):
        p(text, h2)

    def table(rows, widths):
        grid = Table([[Paragraph(escape(str(value)), cell) for value in row] for row in rows], colWidths=widths, repeatRows=1)
        grid.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PALE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LINEBELOW", (0, 0), (-1, -1), .4, colors.HexColor("#D8E3DF")),
        ]))
        story.append(grid)
        story.append(Spacer(1, 8))

    p("LEXFLOW AI / DEMONSTRATION KIT", small)
    p("Sample documents.<br/>A complete walkthrough.", title)
    p(f"Property-purchase intake for one notary office · {DATE}", small)
    p("Use the enclosed German PDF samples to demonstrate extraction, source review, approval and a verified handoff. The guide is in English; all case data is fictional.")
    heading("1. Open the demo")
    p('Open <link href="http://127.0.0.1:8010/presentation/" color="#1E5250">http://127.0.0.1:8010/presentation/</link> on the computer running LexFlow.')
    table([
        ["Account", "Role in the demonstration"],
        ["keller.clerk", "Upload, review sources, resolve conflicts and confirm fields"],
        ["keller.notar", "Approve the reviewed case and create/send its export"],
        ["admin", "Configure reusable workflows and destination mappings"],
    ], [145, 350])
    p("Synthetic demo password for these accounts: <b>lexflow-demo-2026</b>")
    heading("2. Choose one sample scenario")
    table([
        ["Folder", "Upload together", "What it demonstrates"],
        ["property-conflict", "All three PDFs", "Register sheet 812 versus 872 blocks approval"],
        ["property-matching", "All three PDFs", "Matching source values; price confirmation still required"],
        ["scan-review", "The single scanned PDF", "Local OCR and mandatory original-source acknowledgement"],
    ], [128, 112, 255])
    p("Create a separate intake for each scenario. Keep the conflict and matching folders in separate cases; combining them would introduce the intentional conflict again.", small)
    heading("3. Add a case")
    p("Sign in as <b>keller.clerk</b>. Open <b>New intake</b>, enter the subject <b>Kaufvertrag – PDF demonstration</b>, and choose the PDFs from one scenario folder. Select <b>Manual upload</b>, leave <b>Process with LexFlow right away</b> checked, then select <b>Add to inbox</b>.")
    p("The guide itself is presenter material. Upload the sample source PDFs from a scenario folder.", small)

    story.append(PageBreak())
    p("SCENARIO 01 / CONFLICT AND REVIEW", small)
    p("Resolve the disagreement", title)
    p("Upload the three PDFs in <b>property-conflict</b>. The draft contract deliberately disagrees with the questionnaire and register extract.")
    table([
        ["Field", "Expected case data"],
        ["Buyer / birth date", "Anna Schmidt / 12.03.1985"],
        ["Buyer address", "Sonnenallee 24, 80331 München"],
        ["Seller", "Peter Müller"],
        ["Property", "Leopoldstraße 118, 80802 München"],
        ["Purchase price", "850.000,00 EUR"],
        ["Parcel / handover", "1234/5 / 01.12.2026"],
    ], [145, 350])
    table([
        ["Source PDF", "Land-register sheet"],
        ["01_Kaeuferfragebogen.pdf", "München Blatt 812"],
        ["02_Grundbuchauszug.pdf", "München Blatt 812"],
        ["03_Kaufvertragsentwurf.pdf", "München Blatt 872 — intentional conflict"],
    ], [290, 205])
    heading("Review as the clerk")
    p("<b>1.</b> Confirm that the case shows <b>Transfer blocked</b>. Click <b>Land register sheet</b> to see the source evidence and its highlights.")
    p("<b>2.</b> Compare the original PDFs. Choose the <b>Grundbuchauszug</b> candidate with <b>Use this</b>, selecting <b>München Blatt 812</b> for this synthetic case.")
    p("<b>3.</b> Select <b>Confirm</b> for the purchase price. Resolve any remaining blockers shown by the case. It should reach <b>Ready for approval</b>.")
    p("The clerk can review the case; approval is available to the notary or another authorized approver.", small)
    heading("Approve, send and verify")
    p("<b>4.</b> Sign out, sign in as <b>keller.notar</b>, open the case, and select <b>Approve for transfer</b>. The approved values and mappings are frozen.")
    p("<b>5.</b> Select <b>HTTP reference receiver</b>, then <b>Create approved export</b>. Select <b>Send to HTTP destination</b> and show the verified receipt and <b>Delivered</b> status.")
    p("<b>6.</b> Open <b>Audit</b> and show the review, approval, export and receipt events, together with the verified chain.")
    p("The HTTP option uses the independent synthetic reference receiver. It demonstrates the transport contract; it is not a connection to a named notary vendor.", small)

    story.append(PageBreak())
    p("SCENARIOS 02–03 / MATCHING SOURCES AND OCR", small)
    p("Show the other paths", title)
    heading("Matching documents")
    p("Create a new intake using all three PDFs in <b>property-matching</b>. It uses Anna Schmidt's same case data, with <b>München Blatt 812</b> in every source. No land-register conflict should appear. Explicitly confirm the price and any other displayed review requirements before notary approval.")
    heading("An image-only PDF")
    p("Create another intake with <b>scan-review/01_Gescannter_Kaeuferfragebogen.pdf</b>. This is a PDF containing an image, with no searchable text layer. LexFlow must use local OCR to propose the values.")
    table([
        ["Field", "Expected scanned case data"],
        ["Buyer / birth date", "Maria Beispiel / 21.08.1987"],
        ["Buyer address", "Lindenweg 12, 10115 Berlin"],
        ["Seller", "Thomas Muster"],
        ["Property", "Parkstraße 9, 10115 Berlin"],
        ["Purchase price", "425.500,00 EUR"],
        ["Register / parcel", "Berlin Blatt 421 / 123/4"],
        ["Handover", "15.02.2027"],
    ], [145, 350])
    p("Open the original scan and compare every value. Check <b>I read the original and checked its values against the matter.</b>, then select <b>Record source review</b>. Confirm the purchase price and resolve all remaining blockers before approval.")
    p("OCR requires Tesseract with German/English language packs and Poppler. If OCR is unavailable, the original stays available for manual review; record verified values and acknowledge the source before proceeding.", small)
    heading("File export alternative")
    p("For a local file handoff, select <b>Notary software import</b>, create the approved export, then select <b>Run demo receiver and verify receipt</b>. Show the separate export and delivery events.")
    heading("Start the local demo again")
    p("Run this command from the LexFlow project folder:", small)
    p("<b>.venv/bin/python tools/presentation_demo.py<br/>--http-handoff --port 8010</b>")
    p("The command is one line. Restarting reuses the existing isolated demonstration. To repeat a scenario without resetting it, upload the samples into a new intake.", small)
    heading("What to show at the end")
    p("Source-linked values → resolved review requirements → frozen approval → approved export → verified destination receipt → audit history.")
    p("All sample PDFs are visibly marked as synthetic. The register extract and contract draft are demonstration fixtures, with fictional persons, addresses and entries.", small)

    story.append(PageBreak())
    p("OFFICE ANALYTICS / FILTERS AND REVIEW", small)
    p("Read the pilot report", title)
    p('Open <b>Overview → Pilot analytics</b>, or <link href="http://127.0.0.1:8010/#analytics-summary" color="#1E5250">http://127.0.0.1:8010/#analytics-summary</link>. The six summary cards and all report sections use the same filters.')
    heading("Select the cohort")
    p("Choose a receipt period: 7, 14, 30 or 90 days, or <b>Custom dates</b> up to 366 days. Narrow by workflow and intake source, choose the data cohort, then select <b>Apply filters</b>. The demonstration defaults to <b>Synthetic demonstration</b>; live-office data is separate.")
    p("Dates use the configured reporting timezone and select when intakes were received. Current outcomes include their later reviews and receipts. Prior-period comparisons use the preceding interval of equal length with the same filters.")
    table([
        ["Analytics tab", "What to demonstrate"],
        ["Summary", "Intake volume, initial coverage, review/approval backlog, median approval time, cases with receipts, current pending exports, daily trends and workflow links."],
        ["Extraction and review", "First-extraction coverage/prefill, current field states, changed fields, source files, OCR acknowledgements and field review priorities."],
        ["Workload and timing", "Median/P90 elapsed durations and sample counts, unfinished-case aging, source breakdown, paginated case links and recorded reviewer actions."],
        ["Delivery and audit", "Verified receipts, pending/failed exports, HTTP retries, connector reliability, current receipts needing attention and audit coverage/integrity."],
    ], [135, 360])
    heading("Explore and export")
    p("Workflow and source links narrow the report. Open a case reference to inspect the underlying record. Every chart includes <b>View chart data</b> for exact values. <b>Download analytics CSV</b> exports aggregate metadata for the applied filters, without source text or extracted case values.")
    heading("Explain the measurements")
    p("Confidence scores are heuristic. First-extraction coverage stays distinct from fields completed by people. Elapsed approval time includes waiting; staff handling time and manual re-keying errors need separate office measurements. Baselines are context, with no inferred savings. Empty samples show no rate/duration.")
    p("Receipt rate includes all selected exports; the closed-export rate uses delivered + failed records. Pending exports from superseded approvals remain in historical totals and are excluded from the current action list. A created export alone is not delivered.", small)
    heading("Navigation and account recovery")
    p("Arrow keys, Home and End switch section tabs. Section/field links reveal the relevant panel. Tabs preserve unsaved form values, and all sections remain readable without JavaScript. The sign-in eye button toggles password visibility. <b>Forgot password?</b> starts a single-use reset link valid for one hour; the local demo prints the email in the server terminal. Deployed offices must configure SMTP delivery.", small)

    story.append(PageBreak())
    p("STRUCTURED CASES / PRONOTAR32 EXTENSION", small)
    p("Review the complete case", title)
    p("Additional participants, companies, representatives, properties and office references can now be reviewed in LexFlow. Their values and destination mapping revisions are frozen at approval.")
    heading("Demonstrate on a separate unapproved synthetic case")
    p("<b>1. Enable.</b> Sign in as <b>admin</b>, open <b>Connectors</b> and choose <b>Enable structured case exports</b>. This adds one Case JSON download destination per active workflow. Fresh/reset demos need this step; repeating it does not duplicate destinations.")
    p("<b>2. Review details.</b> Open <b>Case details · parties and properties</b>. Add a second participant/company and property, plus a case designation or office reference. Primary names, dates and property identifiers remain linked to Extracted data. Additional records are entered by staff and encrypted.")
    p("<b>3. Check and approve.</b> Inspect <b>Export readiness</b>, resolve source conflicts and complete the required source/price review. As the notary, approve the matter. Readiness checks mappings locally; it does not prove vendor acceptance.")
    p("<b>4. Export.</b> Choose <b>Case JSON — Property purchase</b> or the matching workflow. Inspect the participants and properties arrays and mapping version. The data is frozen and the forms are locked; download alone does not confirm delivery.")
    heading("Compatibility and existing approvals")
    p("Legacy XML/JSON/CSV exports reject cases with saved additional details so data cannot be silently omitted. A mapping enabled after approval requires reopening and reapproving an undelivered case. Existing exports retain their original data and mapping revision. Delivered cases require a new intake for amendments.")
    heading("What is available for the next integration")
    p("The <b>canonical-json-v1</b> adapter and versioned mappings are implemented. The approved-case API returns the complete frozen <b>lexflow.case/1</b> object. <b>ProNotar32 import is not implemented.</b> Obtain the office's exact version, supported import schema, accepted sample files and test environment before implementing and accepting its adapter.")
    p("See <b>integrations/PRONOTAR32_READINESS.txt</b> for the schema, configuration command, extension contract and troubleshooting. All 152 tests passed on PostgreSQL 17 on 30 October; desktop/mobile browser checks covered the new workflow. No vendor was contacted.", small)

    story.append(PageBreak())
    p("PLUGINS AND DESTINATIONS / EXPANSION ROADMAP", small)
    p("Connect the next systems", title)
    p("Extend the existing intake and approved-handoff contracts. The entries below distinguish implemented capabilities from proposed integrations.")
    table([
        ["Capability", "Status in this build"],
        ["Remote scanner / saved email", "Implemented token-authenticated API client; retains originals and deduplicates repeated submissions."],
        ["XML / JSON / CSV exports", "Implemented LexFlow formats. A vendor's exact import schema needs separate mapping and acceptance."],
        ["Case JSON / mapping revisions", "Implemented additional records, frozen profiles, approved-case API and adapter registry. ProNotar32 import remains unavailable."],
        ["Authenticated HTTP destination", "Implemented signed handoff/receipt contract with an independent synthetic reference receiver. No named vendor connection is supplied."],
        ["Outlook / Word / cloud intake", "Proposed native integrations. They require their own client/adapter, authentication and target-system validation."],
    ], [160, 335])
    heading("Recommended implementation order")
    p("<b>1. ProNotar32 adapter.</b> Validate representative case data, obtain the supported import contract and test the adapter against accepted vendor fixtures. Verify identity matching, duplicates and durable receipts.")
    p("<b>2. Plugin access and setup.</b> Add separate scoped credentials, connection tests and queued delivery. These remain proposed capabilities; the rendering/mapping foundation is implemented.")
    p("<b>3. Outlook intake.</b> Submit selected mail/attachments and show case status, preserving the existing review and approval workflow.")
    p("<b>4. Word and cloud sources.</b> Fill agreed Word templates from approved data, then add the office's chosen cloud document source.")
    heading("Current configuration boundaries")
    p("Security → Plugin access issues one active office token for intake and case reads, including approved-case reads. Rotation affects every client. Individual scopes/expiry and background retries remain planned. Connectors shows destinations, mapping revisions, adapter availability and transfers; HTTP setup is operator-managed.")
    p("An adapter must preserve frozen approval/export bytes and duplicate protection. Record delivery only after verified destination acceptance. The reference receiver proves the LexFlow contract; each real vendor needs its own acceptance evidence.")
    heading("Developer and operator handoff")
    p("Read <b>integrations/EXPANSION_GUIDE.txt</b> for API requests/responses, current limits, proposed adapter operations and acceptance cases; <b>integrations/HANDOFF_V1.txt</b> for the exact signed wire contract; and <b>deployment/OPERATIONS.txt</b> for credentials, configuration and recovery.", small)
    p('Platform references: <link href="https://learn.microsoft.com/en-us/office/dev/add-ins/outlook/outlook-add-ins-overview" color="#1E5250">Microsoft Outlook add-ins</link> and <link href="https://learn.microsoft.com/en-us/office/dev/add-ins/word/word-add-ins-programming-overview" color="#1E5250">Microsoft Word add-ins</link>. These describe implementation options; they do not establish an installed LexFlow integration.', small)

    def page_frame(c, doc):
        width, height = A4
        c.setFillColor(GREEN)
        c.rect(0, height - 12, width, 12, fill=1, stroke=0)
        c.setStrokeColor(colors.HexColor("#D8E3DF"))
        c.line(50, 42, width - 50, 42)
        c.setFont("Demo", 7.5)
        c.setFillColor(MUTED)
        c.drawString(50, 27, "LEXFLOW AI / SYNTHETIC DEMONSTRATION / " + DATE.upper())
        c.drawRightString(width - 50, 27, str(doc.page))

    doc = SimpleDocTemplate(str(GUIDE), pagesize=A4, leftMargin=50, rightMargin=50, topMargin=45, bottomMargin=58,
                            title="LexFlow AI demonstration guide", author="Allgift Technologies Ltd")
    doc.build(story, onFirstPage=page_frame, onLaterPages=page_frame)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guide-only", action="store_true", help="Rebuild the guide/archive using existing source PDFs")
    args = parser.parse_args()
    if args.guide_only:
        manifest_path = DOCUMENTS / "manifest.json"
        if not manifest_path.is_file():
            parser.error("Build the sample documents first before using --guide-only.")
        manifest = json.loads(manifest_path.read_text())
        groups = {name: [DOCUMENTS / relative for relative in paths] for name, paths in manifest["scenarios"].items()}
        if any(not path.is_file() for paths in groups.values() for path in paths):
            parser.error("A sample PDF is missing; rebuild the sample documents first.")
    else:
        groups = build_sources()
    build_guide()
    manifest = {
        "data_class": "synthetic", "guide": GUIDE.name,
        "scenarios": {name: [str(path.relative_to(DOCUMENTS)) for path in paths] for name, paths in groups.items()},
        "expected_conflict": {"field": "grundbuch", "questionnaire_and_register": "München Blatt 812", "draft_contract": "München Blatt 872"},
        "matching_case": "All three sources state München Blatt 812; policy review still applies",
        "scan": "Image-only PDF; local OCR and original-source review required",
    }
    (DOCUMENTS / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    archive = OUT / "LexFlow_Demo_Documents.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
        bundle.write(GUIDE, GUIDE.name)
        for path in sorted(DOCUMENTS.rglob("*")):
            if path.is_file():
                bundle.write(path, "demo-documents/" + str(path.relative_to(DOCUMENTS)))
    print(json.dumps({"guide": str(GUIDE), "source_pdfs": sum(map(len, groups.values())), "archive": str(archive)}))


if __name__ == "__main__":
    main()
