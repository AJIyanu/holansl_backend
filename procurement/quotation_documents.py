"""Immutable client quotation PDF rendered from an issued snapshot."""

from decimal import Decimal
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


ASSETS = Path(settings.BASE_DIR) / "ledger" / "assets"


def render_quotation_pdf(snapshot):
    output = BytesIO()
    navy = colors.HexColor("#04035e")
    width = A4[0] - 88
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=44,
        rightMargin=44,
        topMargin=208,
        bottomMargin=48,
    )
    normal = ParagraphStyle(
        "quotation-normal", fontName="Helvetica", fontSize=9, leading=13
    )
    right = ParagraphStyle("quotation-right", parent=normal, alignment=2)
    heading = ParagraphStyle(
        "quotation-heading",
        parent=normal,
        fontName="Helvetica-Bold",
        textColor=colors.white,
    )
    company = {
        **settings.LEDGER_DOCUMENT_COMPANY,
        "phone": settings.LEDGER_DOCUMENT_COMPANY.get("phone")
        or "2348073033767, 2348090964567",
    }

    def p(value, style=normal):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), style)

    def money(value):
        symbol = {"NGN": "₦", "USD": "$", "GBP": "£"}.get(
            snapshot["currency"], snapshot["currency"] + " "
        )
        return f"{symbol}{Decimal(str(value)):,.2f}"

    def header(canvas, doc):
        canvas.saveState()
        top = A4[1]
        edge = A4[0] - 44
        canvas.drawImage(
            str(ASSETS / "logo-mark.png"),
            edge - 180,
            top - 89,
            width=55,
            height=51,
            mask="auto",
        )
        canvas.drawImage(
            str(ASSETS / "logo-wordmark.png"),
            edge - 120,
            top - 87,
            width=120,
            height=58,
            mask="auto",
        )
        contact = "\n".join(
            filter(
                None,
                [
                    f"Website: {company.get('website', '')}"
                    if company.get("website")
                    else "",
                    f"Email: {company.get('email', '')}"
                    if company.get("email")
                    else "",
                    f"WhatsApp: {company['phone']}",
                    "24, Damin Plaza, MKO Abiola Way, Ringroad, Ibadan, Oyo, Nigeria.",
                ],
            )
        )
        block = Paragraph(
            escape(contact).replace("\n", "<br/>"),
            ParagraphStyle("quotation-contact", parent=right, fontSize=8, leading=11),
        )
        _, height = block.wrap(width, 60)
        block.drawOn(canvas, 44, top - 103 - height)
        canvas.setFont("Helvetica-Bold", 21)
        canvas.drawRightString(edge, top - 184, "QUOTATION")
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(edge, 23, f"Page {doc.page}")
        canvas.restoreState()

    story = [
        p("Quoted to:"),
        p(snapshot["client_name"]),
        p(f"Date: {snapshot['quotation_date']}"),
        p(f"Quotation No: {snapshot['reference']}"),
        p(f"Revision: {snapshot.get('version', 1)}"),
    ]
    if snapshot.get("expires_on"):
        story.append(p(f"Expiry date: {snapshot['expires_on']}"))
    story.append(Spacer(1, 20))
    rows = [[p(label, heading) for label in ["S/N", "ITEMS", "PRICE", "QTY", "AMOUNT"]]]
    for index, item in enumerate(snapshot["items"], 1):
        rows.append(
            [
                p(index),
                p(item["description"]),
                p(money(item["unit_price"]), right),
                p(
                    f"{format(Decimal(item['quantity']).normalize(), 'f')} {item['unit']}",
                    right,
                ),
                p(money(item["amount"]), right),
            ]
        )
    table = Table(
        rows,
        colWidths=[width * value for value in [0.07, 0.36, 0.20, 0.17, 0.20]],
        repeatRows=1,
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
    totals = [[p("Sub-total"), p(money(snapshot["subtotal"]), right)]]
    if Decimal(snapshot["discount"]) > 0:
        totals.extend(
            [
                [p("Discount"), p("−" + money(snapshot["discount"]), right)],
                [p("Net subtotal"), p(money(snapshot["net"]), right)],
            ]
        )
    if Decimal(snapshot["tax"]) > 0:
        totals.append(
            [p(f"VAT ({snapshot['tax_rate']}%)"), p(money(snapshot["tax"]), right)]
        )
    totals.append(
        [
            p("TOTAL", heading),
            p(
                money(snapshot["total"]),
                ParagraphStyle("quotation-total", parent=heading, alignment=2),
            ),
        ]
    )
    total_table = Table(totals, colWidths=[width * 0.30, width * 0.30], hAlign="RIGHT")
    total_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -2), colors.HexColor("#e8f0fa")),
                ("BACKGROUND", (0, -1), (-1, -1), navy),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.extend([total_table])
    if snapshot.get("notes"):
        story.extend(
            [
                Spacer(1, 14),
                p(
                    "Notes",
                    ParagraphStyle(
                        "quotation-label", parent=normal, fontName="Helvetica-Bold"
                    ),
                ),
                p(snapshot["notes"]),
            ]
        )
    if snapshot.get("terms"):
        story.extend(
            [
                Spacer(1, 10),
                p(
                    "Terms",
                    ParagraphStyle(
                        "quotation-terms", parent=normal, fontName="Helvetica-Bold"
                    ),
                ),
                p(snapshot["terms"]),
            ]
        )
    story.extend(
        [
            Spacer(1, 48),
            Table(
                [
                    [
                        p(
                            "For the client\nSignature and Date",
                            ParagraphStyle(
                                "quotation-signature", parent=normal, alignment=1
                            ),
                        ),
                        "",
                        p(
                            "For Holan Mgt\nSignature and Date",
                            ParagraphStyle(
                                "quotation-signature-2", parent=normal, alignment=1
                            ),
                        ),
                    ]
                ],
                colWidths=[width * 0.4, width * 0.2, width * 0.4],
                style=TableStyle(
                    [
                        ("LINEABOVE", (0, 0), (0, 0), 0.5, colors.grey),
                        ("LINEABOVE", (2, 0), (2, 0), 0.5, colors.grey),
                    ]
                ),
            ),
        ]
    )
    document.build(story, onFirstPage=header, onLaterPages=header)
    return output.getvalue()
