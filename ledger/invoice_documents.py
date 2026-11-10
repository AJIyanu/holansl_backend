"""Client-facing invoices from confirmed immutable snapshots, with item S/N only."""

from io import BytesIO
from decimal import Decimal
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Spacer,
    KeepTogether,
    Paragraph,
)
from xml.sax.saxutils import escape
from procurement.document_layout import paragraph, ASSETS
from .invoice_units import display_quantity


def render_invoice_pdf(snapshot):
    receipt = snapshot.get("title") == "Receipt"
    output = BytesIO()
    width = A4[0] - 88
    navy = colors.HexColor("#04035e")
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=44,
        rightMargin=44,
        topMargin=208,
        bottomMargin=48,
        invariant=1,
    )
    company = {
        **snapshot["company"],
        "phone": snapshot["company"].get("phone") or "2348073033767, 2348090964567",
    }
    discounted = Decimal(snapshot.get("discount_percent", "0")) > 0

    def header(canvas, document):
        canvas.saveState()
        right = A4[0] - 44
        top = A4[1]
        canvas.drawImage(
            str(ASSETS / "logo-mark.png"),
            right - 180,
            top - 89,
            width=55,
            height=51,
            mask="auto",
        )
        canvas.drawImage(
            str(ASSETS / "logo-wordmark.png"),
            right - 120,
            top - 87,
            width=120,
            height=58,
            mask="auto",
        )
        contact = "\n".join(
            f"{label}: {company[key]}"
            for key, label in [
                ("website", "Website"),
                ("email", "Email"),
                ("phone", "WhatsApp"),
            ]
            if company.get(key)
        )
        contact += "\n24, Damin Plaza, MKO Abiola Way, Ringroad, Ibadan, Oyo, Nigeria."
        style = ParagraphStyle(
            "contact", fontName="Holan", fontSize=8, leading=11, alignment=2
        )
        block = Paragraph(escape(contact).replace("\n", "<br/>"), style)
        _, height = block.wrap(width, 60)
        block.drawOn(canvas, 44, top - 103 - height)
        canvas.setFont("HolanBold", 21)
        canvas.drawRightString(right, top - 184, "RECEIPT" if receipt else "INVOICE")
        canvas.setFont("Holan", 7)
        canvas.drawRightString(right, 23, f"Page {document.page}")
        canvas.restoreState()

    def money(value):
        symbol = {"NGN": "₦", "USD": "$", "GBP": "£"}.get(
            snapshot["currency"], snapshot["currency"] + " "
        )
        return f"{symbol}{Decimal(value):,.2f}"

    story = [
        paragraph("Received from:" if receipt else "Billed to:"),
        paragraph(snapshot["billed_to"]),
    ]
    if snapshot["billing_address"]:
        story.append(paragraph(snapshot["billing_address"]))
    story += [
        paragraph("Date: " + snapshot["date"]),
        paragraph(
            ("Receipt No: " if receipt else "Invoice No: ") + snapshot["reference"]
        ),
        Spacer(1, 20),
    ]
    if receipt:
        for label, value in [
            ("Payment method", snapshot["payment_method"].replace("_", " ")),
            ("Receiving account", snapshot["account_name"]),
            ("Bank", snapshot["bank_name"]),
            ("Payment reference", snapshot["payment_reference"]),
            ("Invoice reference", snapshot["invoice_reference"]),
        ]:
            if value:
                story.append(paragraph(f"{label}: {value}"))
        story.append(Spacer(1, 12))
    heading = ParagraphStyle(
        "table-heading",
        fontName="HolanBold",
        fontSize=9,
        leading=13,
        textColor=colors.white,
    )
    right_style = ParagraphStyle(
        "number", fontName="Holan", fontSize=9, leading=13, alignment=2
    )

    def number(value):
        return Paragraph(escape(str(value)), right_style)

    rows = [
        [
            Paragraph(label, heading)
            for label in ["S/N", "ITEMS", "PRICE", "QTY", "AMOUNT"]
        ]
    ]
    for index, item in enumerate(snapshot["items"], 1):
        # Older immutable snapshots have only the full internal reference.
        # Derive its serial suffix for display without modifying stored history.
        serial = str(item.get("reference", "")).rsplit(".", 1)[-1]
        serial = serial if serial.isdecimal() else str(index)
        rows.append(
            [
                paragraph(serial),
                paragraph(item["description"]),
                number(money(item["unit_price"])),
                number(display_quantity(item["quantity"], item["unit"])),
                number(
                    money(
                        item.get("gross_amount", item["amount"])
                        if discounted
                        else item["amount"]
                    )
                ),
            ]
        )
    table = Table(
        rows,
        colWidths=[width * v for v in [0.07, 0.36, 0.20, 0.17, 0.20]],
        repeatRows=1,
        splitInRow=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), navy),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#dddddd")),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story.append(table)
    total_style = ParagraphStyle(
        "total", parent=right_style, fontName="HolanBold", textColor=colors.white
    )
    total_rows = [
        [
            paragraph("Sub-total"),
            number(money(snapshot.get("gross_subtotal", snapshot["subtotal"]))),
        ]
    ]
    if discounted:
        rate = (
            format(Decimal(snapshot["discount_percent"]), "f").rstrip("0").rstrip(".")
            if "." in snapshot["discount_percent"]
            else snapshot["discount_percent"]
        )
        total_rows += [
            [
                paragraph(f"Discount ({rate}%)"),
                number("−" + money(snapshot["discount_amount"])),
            ],
            [paragraph("Net subtotal"), number(money(snapshot["subtotal"]))],
        ]
    total_rows += [
        [
            paragraph(
                f"VAT paid ({format(Decimal(snapshot.get('vat_rate', '7.5')).normalize(), 'f')}%)"
                if receipt and snapshot["vat_paid"]
                else "VAT not paid"
                if receipt
                else f"VAT ({format(Decimal(snapshot.get('vat_rate', '7.5')).normalize(), 'f')}%)"
            ),
            number(money(snapshot["vat"])),
        ]
    ]
    if receipt:
        total_rows += [
            [
                paragraph(
                    f"Withholding tax ({format(Decimal(snapshot.get('withholding_rate', '2')).normalize(), 'f')}%)"
                    if snapshot["withholding_applied"]
                    else "Withholding tax not applied"
                ),
                number(
                    "−" + money(snapshot["withholding"])
                    if snapshot["withholding_applied"]
                    else money("0")
                ),
            ]
        ]
    total_rows += [
        [
            Paragraph("AMOUNT RECEIVED" if receipt else "TOTAL", heading),
            Paragraph(escape(money(snapshot["total"])), total_style),
        ],
    ]
    totals = Table(
        total_rows,
        colWidths=[width * 0.30, width * 0.30],
        hAlign="RIGHT",
    )
    totals.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -2), colors.HexColor("#e8f0fa")),
                ("BACKGROUND", (0, -1), (-1, -1), navy),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.append(KeepTogether([totals]))
    if snapshot["notes"]:
        story += [Spacer(1, 14), paragraph(snapshot["notes"])]
    if receipt:
        story += [
            Spacer(1, 16),
            paragraph("Amount received in words: " + snapshot["amount_in_words"]),
        ]
    signature_style = ParagraphStyle(
        "signature", fontName="Holan", fontSize=9, leading=13, alignment=1
    )
    signature = Table(
        [
            [
                Paragraph("For the client<br/>Signature and Date", signature_style),
                "",
                Paragraph("For Holan Mgt<br/>Signature and Date", signature_style),
            ]
        ],
        colWidths=[width * 0.40, width * 0.20, width * 0.40],
    )
    signature.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (0, 0), 0.5, colors.grey),
                ("LINEABOVE", (2, 0), (2, 0), 0.5, colors.grey),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(KeepTogether([Spacer(1, 48), signature]))
    doc.build(story, onFirstPage=header, onLaterPages=header)
    return output.getvalue()
