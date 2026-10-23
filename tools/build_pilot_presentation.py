#!/usr/bin/env python3
"""Create an editable PowerPoint and matching PDF using native text/vector shapes.

The deck describes the implemented synthetic presentation scope. It does not
claim a vendor connection, deployed cloud environment or regulatory sign-off.
"""
from pathlib import Path
import json
import textwrap
from xml.etree import ElementTree as E
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "presentation"
OUT.mkdir(exist_ok=True)
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
E.register_namespace("p", P)
E.register_namespace("a", A)
E.register_namespace("r", R)
EMU = 914400
INK, PETROL, PAPER, WHITE, MUTED, GOLD = "142D2C", "1E5250", "F3F5F1", "FFFFFF", "5D6A67", "F4E4A1"

SLIDES = [
    ("LEXFLOW AI / PILOT DEMONSTRATION", "From documents to an approved handoff", "Secure workflow automation for a dedicated notary office", [
        ("The workflow", "Receive documents. Extract structured values. Resolve uncertainty. Approve a frozen export. Confirm its receipt."),
        ("The demonstration", "One office, synthetic property-purchase documents and an independent reference HTTP destination.")]),
    ("01 / A CONTAINED FIRST USE CASE", "Property purchase intake", "Prove a complete, reviewable workflow before expanding integrations.", [
        ("Inputs and output", "Digital PDFs, scanned PDFs/images and saved email produce a structured property-purchase case and an approved XML, JSON or CSV handoff."),
        ("Scope boundary", "A dedicated-office prototype has PostgreSQL concurrency, HTTP, scanner and restore evidence. Vendor acceptance, client hosting and office measurements remain.")]),
    ("02 / THE EMPLOYEE WORKFLOW", "Seven visible stages", "Every consequential decision stays visible to the office.", [
        ("Receive > Classify > Extract", "Keep incoming evidence with the case. Identify the workflow and propose values with their source locations."),
        ("Validate > Review > Export > Audit", "Flag conflict and missing information. Require human approval. Record the export and a separate verified receipt.")]),
    ("03 / THE FLAGSHIP EXAMPLE", "A disagreement the office must resolve", "Synthetic purchase documents intentionally disagree on the land-register sheet.", [
        ("Agreed case data", "Buyer: Anna Schmidt\nSeller: Peter Müller\nPurchase price: EUR 850,000\nQuestionnaire and register: München Blatt 812"),
        ("A meaningful exception", "The draft contract states Blatt 872. The conflict blocks approval until a reviewer chooses or corrects the value. The price also needs explicit confirmation.")]),
    ("04 / HUMAN CONTROL", "Review first. Freeze the decision.", "Confidence supports review; it does not authorize a destination write.", [
        ("The clerk", "Compare sources, correct values and confirm required policy fields. Source selections update case data. Reusable labels and mappings are edited separately by an administrator."),
        ("The approver", "Approve only after all blockers are resolved. The snapshot freezes values, mappings and approval identity. Stale edits cannot undo that approval.")]),
    ("05 / A TRUTHFUL HANDOFF", "A signed receipt proves the handoff", "Actual HTTP transport to an independent reference destination; vendor acceptance remains separate.", [
        ("Create the approved export", "Generate a checksummed payload from the frozen approval. Export a file or send authenticated JSON. Repeated requests reuse the same approval-bound idempotency key."),
        ("Confirm the destination", "The HTTP receiver commits the payload before returning a signed receipt. Wrong keys or receipts keep delivery pending. A lost-response retry retrieves the original receipt without a duplicate import.")]),
    ("06 / PROTECTION IN THE BUILD", "Controls that can be demonstrated", "Show the controls and their practical limits.", [
        ("Confidentiality and approval", "Per-office encryption covers source files, source text, structured case values, revisions and audit metadata. Production refuses development secrets. Review and approval share a locking protocol."),
        ("Traceability and retention", "Audit events have a signed hash chain. Separately held checkpoints detect an older database replay. Encrypted backups restore sources, private fields, approvals and export evidence.")]),
    ("07 / ONE OFFICE, ONE DEPLOYMENT", "A dedicated pilot boundary", "Prepare separate database, storage and keys for each office.", [
        ("Presentation environment", "The launcher creates one office, a separate database, vault and keys. Local OCR reads scans; original-source review remains mandatory. A remote scanner/email client uses only the intake API."),
        ("Deployment package", "Concurrency and complete encrypted restore passed on local PostgreSQL. A dedicated HTTPS stack is supplied. Its container build, client hosting, TLS and operational recovery still need validation.")]),
    ("08 / PILOT EVIDENCE", "Measure results without mixing demo data", "Demonstration history is clearly labelled and excluded from live-pilot measurements.", [
        ("What to measure", "The synthetic corpus passed 64 checks across 12 cases, including scans, missing values and conflicts. Office metrics cover corrections, review time, confirmed handoffs and audit completeness."),
        ("How to compare", "Synthetic pass rates are regression evidence, not office accuracy. Record a manual baseline and a separate sanitized cohort. Measure handling time separately from elapsed approval time.")]),
    ("09 / LIVE DEMONSTRATION", "A seven-minute walkthrough", "Use /presentation/ as your presenter guide.", [
        ("Minutes 1–4", "Open the unprocessed intake. Process the documents. Show the Blatt 812 / 872 conflict. Resolve the source choice and confirm the price as the clerk."),
        ("Minutes 5–7", "Sign in as the notary. Approve, create the HTTP export and send it. Show its verified receipt and audit trail. Optionally demonstrate scanned intake and mandatory source review.")]),
    ("10 / WHAT IS NEXT", "A precise client pilot agreement", "Keep the first real integration as narrow as the demonstrated workflow.", [
        ("Decide with the office", "Confirm the property-purchase schema, required approvals, dedicated hosting boundary, retention policy, representative sanitized documents and exact destination interface."),
        ("Validate before live use", "Validate the actual vendor import and receipt, client deployment and restore procedures. Evaluate sanitized office samples against agreed limits. Native Office plugins and SSO remain roadmap items.")]),
    ("LEXFLOW AI / THE NEXT STEP", "Select one destination. Validate one workflow.", "A controlled, demonstrable foundation for the first office pilot.", [
        ("What you can present today", "A working case from PDF or scan to reviewed data, frozen approval, authenticated HTTP handoff and signed receipt, with PostgreSQL, recovery and extraction evidence."),
        ("What the client supplies next", "A supported destination interface and test environment, sanitized examples, an accepted deployment boundary and agreed success criteria.")]),
]


def element(parent, namespace, name, attrs=None):
    return E.SubElement(parent, "{" + namespace + "}" + name, attrs or {})


def slide_xml(index, kicker, title, subtitle, cards):
    root = E.Element("{" + P + "}sld")
    common = element(root, P, "cSld")
    bg = element(element(common, P, "bg"), P, "bgPr")
    element(element(bg, A, "solidFill"), A, "srgbClr", {"val": INK if index in (1, len(SLIDES)) else PAPER})
    tree = element(common, P, "spTree")
    group = element(tree, P, "nvGrpSpPr")
    element(group, P, "cNvPr", {"id": "1", "name": ""})
    element(group, P, "cNvGrpSpPr")
    element(group, P, "nvPr")
    element(tree, P, "grpSpPr")
    dark = index in (1, len(SLIDES))
    counter = 1

    def shape(x, y, w, h, fill, text="", size=18, color=INK, bold=False):
        nonlocal counter
        counter += 1
        shape = element(tree, P, "sp")
        nv = element(shape, P, "nvSpPr")
        element(nv, P, "cNvPr", {"id": str(counter), "name": f"LexFlow {counter}"})
        element(nv, P, "cNvSpPr", {"txBox": "1"})
        element(nv, P, "nvPr")
        props = element(shape, P, "spPr")
        xf = element(props, A, "xfrm")
        element(xf, A, "off", {"x": str(int(x*EMU)), "y": str(int(y*EMU))})
        element(xf, A, "ext", {"cx": str(int(w*EMU)), "cy": str(int(h*EMU))})
        element(element(props, A, "prstGeom", {"prst": "rect"}), A, "avLst")
        if fill:
            element(element(props, A, "solidFill"), A, "srgbClr", {"val": fill})
        else:
            element(props, A, "noFill")
        element(element(props, A, "ln"), A, "noFill")
        body = element(shape, P, "txBody")
        element(body, A, "bodyPr", {"wrap": "square", "anchor": "t", "lIns": "0", "rIns": "0", "tIns": "0", "bIns": "0"})
        element(body, A, "lstStyle")
        for line in text.split("\n"):
            para = element(body, A, "p")
            run = element(para, A, "r")
            rp = element(run, A, "rPr", {"lang": "en-GB", "sz": str(int(size*100)), "b": "1" if bold else "0"})
            element(element(rp, A, "solidFill"), A, "srgbClr", {"val": color})
            element(rp, A, "latin", {"typeface": "Arial"})
            element(run, A, "t").text = line
            element(para, A, "endParaRPr", {"lang": "en-GB", "sz": str(int(size*100))})
    shape(0.58, 0.5, 12, .4, None, kicker, 12, GOLD if dark else PETROL, True)
    shape(.58, 1.15, 12.15, 1.25, None, title, 36, WHITE if dark else INK, True)
    shape(.6, 2.55, 11.8, .65, None, subtitle, 19, "C3D3D0" if dark else MUTED)
    for column, (heading, body) in enumerate(cards):
        x = .6 + column*6.15
        shape(x, 3.55, 5.95, 2.7, PETROL if dark else WHITE)
        shape(x+.24, 3.82, 5.43, .5, None, heading, 20, GOLD if dark else PETROL, True)
        shape(x+.24, 4.4, 5.43, 1.64, None, body, 17, WHITE if dark else INK)
    shape(.6, 7.02, 11.8, .24, None, "ALLGIFT TECHNOLOGIES LTD  /  SYNTHETIC PRESENTATION DEMO  /  22 OCTOBER 2026", 9, "C3D3D0" if dark else MUTED)
    shape(12.15, 7.0, .55, .3, None, f"{index:02d}", 12, GOLD if dark else PETROL, True)
    element(element(root, P, "clrMapOvr"), A, "masterClrMapping")
    return E.tostring(root, encoding="utf-8", xml_declaration=True)


def rels(entries):
    root = E.Element("Relationships", xmlns=REL)
    for rid, kind, target in entries:
        E.SubElement(root, "Relationship", Id=rid, Type=R+"/"+kind, Target=target)
    return E.tostring(root, encoding="utf-8", xml_declaration=True)


def build_pptx():
    original = Path("/home/allgift/Downloads/LexFlow_AI_Client_Presentation.pptx")
    with ZipFile(original) as template:
        theme = template.read("ppt/theme/theme1.xml")
    types = E.Element("Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types")
    E.SubElement(types, "Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
    E.SubElement(types, "Default", Extension="xml", ContentType="application/xml")
    parts = {"/ppt/presentation.xml": "presentation.main", "/ppt/slideMasters/slideMaster1.xml": "slideMaster",
             "/ppt/slideLayouts/slideLayout1.xml": "slideLayout"}
    for i in range(1, len(SLIDES)+1):
        parts[f"/ppt/slides/slide{i}.xml"] = "slide"
    for path, kind in parts.items():
        E.SubElement(types, "Override", PartName=path, ContentType=f"application/vnd.openxmlformats-officedocument.presentationml.{kind}+xml")
    E.SubElement(types, "Override", PartName="/ppt/theme/theme1.xml", ContentType="application/vnd.openxmlformats-officedocument.theme+xml")
    presentation = E.Element("{"+P+"}presentation")
    masters = element(presentation, P, "sldMasterIdLst")
    element(masters, P, "sldMasterId", {"id": "2147483648", "{"+R+"}id": "rId1"})
    slides = element(presentation, P, "sldIdLst")
    for i in range(1, len(SLIDES)+1):
        element(slides, P, "sldId", {"id": str(255+i), "{"+R+"}id": f"rId{i+1}"})
    element(presentation, P, "sldSz", {"cx": "12192000", "cy": "6858000", "type": "screen16x9"})
    element(presentation, P, "notesSz", {"cx": "6858000", "cy": "9144000"})
    master = E.Element("{"+P+"}sldMaster")
    common = element(master, P, "cSld")
    tree = element(common, P, "spTree")
    nv = element(tree, P, "nvGrpSpPr")
    element(nv, P, "cNvPr", {"id": "1", "name": ""}); element(nv, P, "cNvGrpSpPr"); element(nv, P, "nvPr")
    element(tree, P, "grpSpPr")
    element(master, P, "clrMap", {**{f"accent{i}": f"accent{i}" for i in range(1,7)}, "bg1": "lt1", "bg2": "lt2", "tx1": "dk1", "tx2": "dk2", "hlink": "hlink", "folHlink": "folHlink"})
    element(element(master, P, "sldLayoutIdLst"), P, "sldLayoutId", {"id": "2147483649", "{"+R+"}id": "rId1"})
    styles = element(master, P, "txStyles")
    for style in ("titleStyle", "bodyStyle", "otherStyle"): element(styles, P, style)
    layout = E.Element("{"+P+"}sldLayout", {"type": "blank", "preserve": "1"})
    layout_common = element(layout, P, "cSld", {"name": "LexFlow blank"})
    layout_common.append(E.fromstring(E.tostring(tree)))
    element(element(layout, P, "clrMapOvr"), A, "masterClrMapping")
    with ZipFile(OUT/"LexFlow_Pilot_Presentation.pptx", "w", ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", E.tostring(types, encoding="utf-8", xml_declaration=True))
        z.writestr("_rels/.rels", rels([("rId1", "officeDocument", "ppt/presentation.xml")]))
        z.writestr("ppt/presentation.xml", E.tostring(presentation, encoding="utf-8", xml_declaration=True))
        z.writestr("ppt/_rels/presentation.xml.rels", rels([("rId1", "slideMaster", "slideMasters/slideMaster1.xml")] + [(f"rId{i+1}", "slide", f"slides/slide{i}.xml") for i in range(1,len(SLIDES)+1)]))
        z.writestr("ppt/slideMasters/slideMaster1.xml", E.tostring(master, encoding="utf-8", xml_declaration=True))
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"), ("rId2", "theme", "../theme/theme1.xml")]))
        z.writestr("ppt/slideLayouts/slideLayout1.xml", E.tostring(layout, encoding="utf-8", xml_declaration=True))
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", rels([("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")]))
        z.writestr("ppt/theme/theme1.xml", theme)
        for index, data in enumerate(SLIDES, 1):
            z.writestr(f"ppt/slides/slide{index}.xml", slide_xml(index, *data))
            z.writestr(f"ppt/slides/_rels/slide{index}.xml.rels", rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml")]))


def build_pdf():
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import HexColor
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    normal, bold = "Helvetica", "Helvetica-Bold"
    font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if font.exists():
        pdfmetrics.registerFont(TTFont("LexFlow", str(font)))
        pdfmetrics.registerFont(TTFont("LexFlowBold", str(font.with_name("DejaVuSans-Bold.ttf"))))
        normal, bold = "LexFlow", "LexFlowBold"
    width, height = 960, 540
    c = canvas.Canvas(str(OUT/"LexFlow_Pilot_Presentation.pdf"), pagesize=(width,height))
    c.setTitle("LexFlow AI — dedicated-office pilot demonstration")
    c.setAuthor("Allgift Technologies Ltd")
    for index, (kicker, title, subtitle, cards) in enumerate(SLIDES, 1):
        dark = index in (1, len(SLIDES))
        c.setFillColor(HexColor("#"+(INK if dark else PAPER))); c.rect(0,0,width,height,fill=1,stroke=0)
        def text(x, top, value, size, color, font_name=normal, max_width=870):
            c.setFont(font_name,size);c.setFillColor(HexColor("#"+color))
            lines=[]
            for paragraph in value.split("\n"):
                lines.extend(simpleSplit(paragraph,font_name,size,max_width))
            y=height-top
            for line in lines:
                c.drawString(x,y,line); y-=size*1.34
            return y
        text(44,45,kicker,10,GOLD if dark else PETROL,bold)
        text(44,106,title,30,WHITE if dark else INK,bold)
        text(44,191,subtitle,14,"C3D3D0" if dark else MUTED)
        for column,(heading,body) in enumerate(cards):
            x=44+column*440
            c.setFillColor(HexColor("#"+(PETROL if dark else WHITE)));c.roundRect(x,89,426,195,7,fill=1,stroke=0)
            text(x+18,284,heading,15,GOLD if dark else PETROL,bold,390)
            last_y=text(x+18,321,body,12.5,WHITE if dark else INK,normal,390)
            if last_y < 95: raise ValueError(f"PDF slide {index} overflows")
        text(44,518,"ALLGIFT TECHNOLOGIES LTD / SYNTHETIC DEMONSTRATION / 22 OCTOBER 2026",7,"C3D3D0" if dark else MUTED)
        text(895,518,f"{index:02d}",9,GOLD if dark else PETROL,bold)
        c.showPage()
    c.save()


if __name__ == "__main__":
    build_pptx(); build_pdf()
    (OUT/"slide-content.json").write_text(json.dumps(SLIDES,indent=2,ensure_ascii=False))
    print(f"Created {len(SLIDES)} editable PowerPoint slides and matching PDF in {OUT}")
