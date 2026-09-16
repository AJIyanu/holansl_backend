"""Private evidence and immutable financial/report exports using existing providers."""

import hashlib
import uuid
from io import BytesIO
from xml.sax.saxutils import escape
from django.db import transaction
from rest_framework.exceptions import APIException
from crm.document_services import validate_document_upload, calculate_document_checksum
from crm.storage import get_document_storage, DocumentStorageError
from ledger import models as m
from ledger.services.common import (
    require,
    full_scope,
    get_record,
    make,
    check,
    digest,
    evidence,
)


class StorageUnavailable(APIException):
    status_code = 503
    default_code = "storage_unavailable"
    default_detail = "Private storage is unavailable. Retry later."


def store(
    user, raw, filename, mime, party=None, record=None, statement=None, fingerprint=""
):
    doc_id = uuid.uuid4()
    checksum = hashlib.sha256(raw).hexdigest()
    try:
        provider = get_document_storage("supabase")
        stored = provider.upload(
            document_id=doc_id,
            party_id=party.pk if party else doc_id,
            category="LEDGER",
            filename=filename,
            mime_type=mime,
            size_bytes=len(raw),
            file_object=BytesIO(raw),
        )
    except DocumentStorageError:
        raise StorageUnavailable() from None
    try:
        return make(
            m.LedgerDocument,
            user,
            id=doc_id,
            record=record,
            party=party,
            statement=statement,
            filename=filename,
            mime_type=mime,
            checksum=checksum,
            size_bytes=len(raw),
            storage_provider=stored.provider,
            external_file_id=stored.external_file_id,
            storage_path=stored.storage_path,
            fingerprint=fingerprint,
        )
    except Exception:
        try:
            provider.delete(
                external_file_id=stored.external_file_id,
                storage_path=stored.storage_path,
            )
        except DocumentStorageError:
            pass
        raise


def upload(user, data):
    require(user, "add_ledgerdocument")
    record = get_record(user, data["record"].pk) if data.get("record") else None
    party = data.get("party") or (record.party if record else None)
    check(
        not record or party == record.party,
        "Evidence client differs from financial record.",
    )
    file = data["file"]
    filename, mime, size = validate_document_upload(file)
    calculate_document_checksum(file)
    file.seek(0)
    return store(user, file.read(), filename, mime, party, record)


def download(user, doc):
    require(user, "download_ledgerdocument")
    if doc.statement_id:
        full_scope(user, "view_clientstatement")
    else:
        evidence(user, doc)
    try:
        return get_document_storage(doc.storage_provider).download(
            external_file_id=doc.external_file_id,
            storage_path=doc.storage_path,
            filename=doc.filename,
            mime_type=doc.mime_type,
        )
    except DocumentStorageError:
        raise StorageUnavailable() from None


def simple_pdf(title, subtitle, headers, rows, totals):
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Table,
        TableStyle,
        Spacer,
    )
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape

    output = BytesIO()
    styles = getSampleStyleSheet()

    def p(text):
        return Paragraph(escape(str(text)), styles["BodyText"])

    content = [Paragraph(escape(title), styles["Title"]), p(subtitle), Spacer(1, 12)]
    table = Table(
        [[p(x) for x in headers]] + [[p(x) for x in row] for row in rows], repeatRows=1
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbe7f3")),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    content.extend([table, Spacer(1, 12)])
    content.extend(p(f"{label}: {value}") for label, value in totals)
    SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        leftMargin=30,
        rightMargin=30,
        topMargin=30,
        bottomMargin=30,
    ).build(content)
    return output.getvalue()


def generate(user, record=None, statement=None, format="PDF"):
    require(user, "generate_financialrecord" if record else "export_clientstatement")
    if record:
        record = get_record(user, record.pk)
        check(
            record.status == "POSTED" and record.kind in {"INVOICE", "RECEIPT"},
            "Generate official files only for posted invoices or receipts.",
        )
        from procurement.document_layout import render_pdf, company_profile

        company = company_profile()
        check(company.get("address"), "Configure the official company office address.")
        fingerprint = digest(
            {
                "record": record.pk,
                "content": record.registered_version.snapshot,
                "company": company,
                "format": format,
                "template": "ledger-v1",
            }
        )
    else:
        full_scope(user, "view_clientstatement")
        fingerprint = digest(
            {
                "statement": statement.pk,
                "snapshot": statement.fingerprint,
                "format": format,
                "template": "ledger-v1",
            }
        )
    existing = list(
        m.LedgerDocument.objects.filter(fingerprint=fingerprint).order_by("filename")
    )
    if existing:
        return existing
    if record:
        if record.kind == "INVOICE":
            raw = render_pdf(
                record.registered_version.snapshot, company=company, draft=False
            )
        else:
            raw = simple_pdf(
                "RECEIPT",
                f"{record.reference} | {record.party_name} | {record.date}",
                ["Currency", "Amount received", "Payment reference"],
                [
                    [
                        record.currency.code,
                        str(record.amount),
                        record.receipt.payment_reference,
                    ]
                ],
                [("Office", company["address"])],
            )
        filename = record.reference
        party = record.party
    else:
        data = statement.data
        raw = simple_pdf(
            "Reconciliation and Statement",
            f"{data['client_name']} | {data['currency']} | {data['date_from']} to {data['date_to']}",
            ["Date", "Reference", "Description", "Debit", "Credit", "Balance"],
            [
                [
                    r["date"],
                    r["reference"],
                    r["description"],
                    r["debit"],
                    r["credit"],
                    r["running_balance"],
                ]
                for r in data["rows"]
            ],
            [
                (label, data[field])
                for label, field in [
                    ("Opening balance", "opening_balance"),
                    ("Invoiced", "invoice_total"),
                    ("Cash received", "cash_received_total"),
                    ("Withheld", "withheld_total"),
                    ("Closing balance", "closing_balance"),
                ]
            ],
        )
        filename = f"Reconciliation-and-Statement-{statement.pk}"
        party = statement.account.party
    payloads = [(raw, filename + ".pdf", "application/pdf")]
    if format == "JPG":
        import pypdfium2 as pdfium

        payloads = []
        with pdfium.PdfDocument(raw) as pdf:
            for index in range(len(pdf)):
                page = pdf[index]
                try:
                    bitmap = page.render(scale=2)
                    try:
                        output = BytesIO()
                        bitmap.to_pil().convert("RGB").save(
                            output, format="JPEG", quality=92
                        )
                        payloads.append(
                            (
                                output.getvalue(),
                                f"{filename}-p{index + 1:03d}.jpg",
                                "image/jpeg",
                            )
                        )
                    finally:
                        bitmap.close()
                finally:
                    page.close()
    created = []
    try:
        with transaction.atomic():
            for content, name, mime in payloads:
                created.append(
                    store(
                        user, content, name, mime, party, record, statement, fingerprint
                    )
                )
        return created
    except Exception:
        # A failed later JPG page must not leave an incomplete reusable export.
        for doc in created:
            try:
                get_document_storage(doc.storage_provider).delete(
                    external_file_id=doc.external_file_id, storage_path=doc.storage_path
                )
            except DocumentStorageError:
                pass
        raise
