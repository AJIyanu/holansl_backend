"""Waybill PDF rendered from its immutable issue snapshot."""

from io import BytesIO
from pathlib import Path
from decimal import Decimal
from xml.sax.saxutils import escape

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ASSETS = Path(settings.BASE_DIR) / "ledger" / "assets"


def render_waybill_pdf(snapshot):
    output = BytesIO()
    navy = colors.HexColor("#04035e")
    width = A4[0] - 88
    normal = ParagraphStyle(
        "waybill-normal", fontName="Helvetica", fontSize=9, leading=13
    )
    right = ParagraphStyle("waybill-right", parent=normal, alignment=2)
    heading = ParagraphStyle(
        "waybill-heading",
        parent=normal,
        fontName="Helvetica-Bold",
        textColor=colors.white,
    )
    bold = ParagraphStyle("waybill-bold", parent=normal, fontName="Helvetica-Bold")
    company = settings.LEDGER_DOCUMENT_COMPANY

    def p(value, style=normal):
        return Paragraph(escape(str(value or "")).replace("\n", "<br/>"), style)

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
                    f"Website: {company.get('website', '')}",
                    f"Email: {company.get('email', '')}",
                    "WhatsApp: 2348073033767, 2349037256699",
                    "24, Damin Plaza, MKO Abiola Way, Ringroad, Ibadan, Oyo, Nigeria.",
                ],
            )
        )
        block = Paragraph(
            escape(contact).replace(
                "\n",
                "<br/>",
            ),
            ParagraphStyle("waybill-contact", parent=right, fontSize=8, leading=11),
        )
        _, height = block.wrap(width, 60)
        block.drawOn(canvas, 44, top - 103 - height)
        canvas.setFont("Helvetica-Bold", 21)
        canvas.drawRightString(edge, top - 184, "WAYBILL")
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(edge, 23, f"Page {doc.page}")
        canvas.restoreState()

    story = [
        p(f"Delivered to: {snapshot['delivered_to']}"),
        p(f"Date: {snapshot['waybill_date']}"),
        p(f"Waybill No: {snapshot['reference']}"),
        Spacer(1, 20),
    ]
    rows = [[p(label, heading) for label in ["S/N", "DESCRIPTION", "QTY", "UOM"]]]
    for index, item in enumerate(snapshot["items"], 1):
        rows.append(
            [
                p(index),
                p(item["description"]),
                p(format(Decimal(str(item["quantity"])).normalize(), "f"), right),
                p(item["unit"], right),
            ]
        )
    table = Table(
        rows,
        colWidths=[width * 0.08, width * 0.57, width * 0.15, width * 0.20],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), navy),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 1), (-1, -1), 0.4, colors.HexColor("#dddddd")),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story.extend([table, Spacer(1, 28)])
    details = [
        [p("Delivery Personnel:", bold), p(snapshot["delivery_personnel"])],
        [p("Delivery Address:", bold), p(snapshot["delivery_address"])],
    ]
    for label, key in [
        ("Logistics Method:", "logistics_method"),
        ("Driver:", "driver_name"),
        ("Vehicle:", "vehicle_details"),
        ("Notes:", "notes"),
    ]:
        if snapshot.get(key):
            details.append([p(label, bold), p(snapshot[key])])
    story.append(
        Table(
            details,
            colWidths=[width * 0.25, width * 0.75],
            style=TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ]
            ),
        )
    )
    story.extend(
        [
            Spacer(1, 35),
            Table(
                [
                    [
                        p("Receiver's Sign\n\n____________________________", normal),
                        p("Date Received\n\n____________________________", normal),
                    ],
                    [
                        p("Name of Signatory\n\n____________________________", normal),
                        p("For Holan Mgt\n\n____________________________", normal),
                    ],
                ],
                colWidths=[width * 0.5, width * 0.5],
                style=TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("TOPPADDING", (0, 0), (-1, -1), 16),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 16),
                    ]
                ),
            ),
        ]
    )
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=44,
        rightMargin=44,
        topMargin=208,
        bottomMargin=48,
    )
    document.build(story, onFirstPage=header, onLaterPages=header)
    return output.getvalue()
