"""Company layouts based on the supplied quotation, invoice and waybill samples."""

from decimal import Decimal, localcontext
from datetime import date
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

ASSETS = Path(__file__).with_name("assets")
pdfmetrics.registerFont(TTFont("Holan", str(ASSETS / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("HolanBold", str(ASSETS / "DejaVuSans-Bold.ttf")))
NAVY = colors.HexColor("#223b65")
STYLE = ParagraphStyle("body", fontName="Holan", fontSize=9, leading=13)


def company_profile():
    return dict(settings.PROCUREMENT_DOCUMENT_COMPANY)


def paragraph(value):
    return Paragraph(escape(str(value or "")).replace("\n", "<br/>"), STYLE)


def display_date(value):
    if not value:
        return ""
    parsed = date.fromisoformat(str(value)[:10])
    return f"{parsed.day} {parsed.strftime('%B %Y')}"


def display_rate(value):
    return format(Decimal(str(value)).normalize(), "f")


def render_pdf(
    snapshot,
    *,
    company,
    draft=True,
    show_logistics=False,
    show_additional_charges=False,
):
    """Render trusted snapshots, escaping all business text and repeating table headers."""
    output = BytesIO()
    kind = snapshot["kind"]
    title = {"QT": "QUOTATION", "WB": "WAYBILL", "IN": "INVOICE"}[kind]
    width, height = 612, 792
    usable = 524
    document = SimpleDocTemplate(
        output,
        pagesize=(width, height),
        leftMargin=44,
        rightMargin=44,
        topMargin=214,
        bottomMargin=65,
    )

    def page_header(canvas, doc):
        canvas.saveState()
        canvas.drawImage(
            str(ASSETS / "logo-mark.png"), 302, 661, width=86, height=79, mask="auto"
        )
        canvas.drawImage(
            str(ASSETS / "logo-wordmark.png"),
            383,
            663,
            width=151,
            height=77,
            mask="auto",
        )
        canvas.setFont("Holan", 8)
        for i, line in enumerate(
            [company["website"], company["email"], company["phone"]]
        ):
            canvas.drawRightString(534, 650 - i * 12, line)
        canvas.setFont("HolanBold", 18)
        canvas.drawRightString(534, 595, title)
        footer = paragraph(company["address"] or "Office address not configured")
        _, footer_height = footer.wrap(usable, 45)
        footer.drawOn(canvas, 44, 26)
        canvas.setFont("Holan", 7)
        canvas.drawString(
            44, 13, f"{snapshot['number']} · Revision {snapshot.get('revision', 1)}"
        )
        canvas.drawRightString(568, 13, f"Page {doc.page}")
        if draft:
            canvas.setFillColor(colors.HexColor("#a22929"))
            canvas.setFont("HolanBold", 11)
            canvas.drawString(44, 751, "DRAFT — NOT VALID FOR ISSUE")
        canvas.restoreState()

    story = [
        paragraph(
            f"{'Delivered to' if kind == 'WB' else 'Billed to'}: {snapshot['client']}"
        ),
        paragraph(f"Date: {display_date(snapshot['date'])}"),
        paragraph(f"{title.title()} No: {snapshot['number']}"),
        Spacer(1, 16),
    ]
    rows = (
        [["S/N", "DESCRIPTION", "QTY", "UOM"]]
        if kind == "WB"
        else [["S/N", "DESCRIPTION", "UNIT PRICE", "QTY", "AMOUNT"]]
    )
    totals = {
        key: Decimal(0)
        for key in ["subtotal", "discount", "tax", "logistics", "additional_charges"]
    }
    currency = snapshot.get("currency", "NGN")

    def money(value):
        return f"{'₦' if currency == 'NGN' else currency + ' '}{value:,.2f}"

    with localcontext() as context:
        context.prec = 40
        for index, line in enumerate(snapshot["lines"], 1):
            quantity = Decimal(line["quantity"])
            if kind == "WB":
                rows.append(
                    [
                        str(index),
                        paragraph(line["description"]),
                        str(quantity),
                        paragraph(line.get("unit", "")),
                    ]
                )
            else:
                price = Decimal(line["unit_price"])
                amount = quantity * price
                rows.append(
                    [
                        str(index),
                        paragraph(line["description"]),
                        money(price),
                        str(quantity),
                        money(amount),
                    ]
                )
                totals["subtotal"] += amount
                for key in ["discount", "tax", "logistics", "additional_charges"]:
                    totals[key] += Decimal(line.get(key) or "0")
        widths = [32, 362, 55, 75] if kind == "WB" else [32, 216, 106, 42, 128]
        table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), "Holan"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "HolanBold"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 1), (-1, -1), 0.5, colors.black),
                    ("TOPPADDING", (0, 0), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                    ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ]
            )
        )
        story += [table, Spacer(1, 32)]
        if kind == "WB":
            for line in [
                f"Delivery personnel: {snapshot.get('logistics', '')}",
                f"Delivered to: {snapshot.get('recipient', '')}",
                f"Address: {snapshot.get('destination', '')}",
                "Receiver's signature: ____________________________________",
                "Name of signatory: ____________________________________",
                "Date: ____________________________________",
            ]:
                story += [paragraph(line), Spacer(1, 18)]
        else:
            total = (
                totals["subtotal"]
                - totals["discount"]
                + totals["tax"]
                + totals["logistics"]
                + totals["additional_charges"]
            )
            tax_rates = {
                str(line.get("tax_rate"))
                for line in snapshot["lines"]
                if line.get("tax_rate") is not None
            }
            discount_rates = {
                str(line.get("discount_value"))
                for line in snapshot["lines"]
                if line.get("discount_type") == "PERCENT"
            }
            labels = {
                "tax": f"Tax ({display_rate(next(iter(tax_rates)))}%)"
                if len(tax_rates) == 1
                else "Tax",
                "discount": f"Discount ({display_rate(next(iter(discount_rates)))}%)"
                if len(discount_rates) == 1
                else "Discount",
                "additional_charges": "Additional Charges",
            }
            summary = [
                [labels.get(key, key.replace("_", " ").title()), money(value)]
                for key, value in totals.items()
                if (key != "logistics" or show_logistics)
                and (key != "additional_charges" or show_additional_charges)
            ]
            summary.append(["TOTAL", money(total)])
            summary_table = Table(summary, colWidths=[146, 166], hAlign="RIGHT")
            summary_table.setStyle(
                TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), "Holan"),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#d9e2f3")),
                        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                        ("TOPPADDING", (0, 0), (-1, -1), 7),
                        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#8eaadb")),
                        ("FONTNAME", (0, -1), (-1, -1), "HolanBold"),
                        ("LINEABOVE", (0, -1), (-1, -1), 2, colors.black),
                    ]
                )
            )
            story.append(summary_table)
            if snapshot.get("valid_until"):
                story += [
                    Spacer(1, 16),
                    paragraph(f"Valid until: {display_date(snapshot['valid_until'])}"),
                ]
            if kind == "IN":
                story += [
                    Spacer(1, 35),
                    paragraph(
                        "Signature and date: ____________________________________"
                    ),
                ]
    document.build(story, onFirstPage=page_header, onLaterPages=page_header)
    return output.getvalue()
