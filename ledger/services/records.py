"""Validated creation and immutable approval snapshots for financial documents."""

from decimal import Decimal
from django.db import transaction
from ledger import models as m
from .common import (
    require,
    make,
    check,
    amount,
    get_record,
    accounts_department,
    evidence,
    event,
    business_date,
    version,
    digest,
    primitive,
)


def lines(record):
    return record.invoice.lines.all()


def base(user, kind, data):
    require(user, "view_financialrecord")
    require(user, "add_" + m.RESOURCE[kind])
    party = data.get("party")
    if kind in {"INVOICE", "SUPPLIER_BILL"}:
        from crm.integrations import validate_party_for_new_business

        check(party is not None, "Select the client or supplier.")
        validate_party_for_new_business(
            party, role="CLIENT" if kind == "INVOICE" else "SUPPLIER"
        )
    if kind in {"INVOICE", "CREDIT", "WITHHOLDING"} or (
        kind == "RECEIPT" and data.get("purpose") == "CLIENT"
    ):
        check(party is not None, "Select the party.")
    request = data.get("request")
    if request:
        from procurement.services import visible_requests

        check(
            visible_requests(user).filter(pk=request.pk).exists(),
            "Procurement request is not accessible.",
        )
        check(
            kind not in {"INVOICE", "CREDIT", "WITHHOLDING"}
            and not (kind == "RECEIPT" and data.get("purpose") == "CLIENT")
            or party is None
            or request.client_id == party.pk,
            "Request and client do not match.",
        )
    currency = data["currency"]
    check(currency.active, "Currency is inactive.")
    doc = evidence(user, data["evidence"], party) if data.get("evidence") else None
    return make(
        m.FinancialRecord,
        user,
        kind=kind,
        party=party,
        party_name=str(party) if party else "",
        currency=currency,
        policy=data["policy"],
        department=accounts_department(),
        date=business_date(data["date"]),
        description=data.get("description", ""),
        reference=data.get("reference", ""),
        request=request,
        evidence=doc,
    )


@transaction.atomic
def create(user, kind, data):
    record = base(user, kind, data)
    total = Decimal(0)
    net = Decimal(0)
    tax = Decimal(0)
    if kind == "INVOICE":
        due = data.get("due_date")
        check(not due or due >= record.date, "Due date precedes invoice date.")
        invoice = make(m.Invoice, user, record=record, due_date=due)
        check(data.get("lines"), "An invoice needs at least one line.")
        for row in data["lines"]:
            qty = amount(row["quantity"])
            price = amount(row["unit_price"])
            discount = amount(row.get("discount", 0), record.currency)
            check(
                qty > 0 and price >= 0 and 0 <= discount <= qty * price,
                "Invalid quantity, price or discount.",
            )
            item = row.get("item")
            quote = row.get("quote_line")
            po = row.get("po_line")
            if item:
                check(
                    record.request_id == item.request_id,
                    "Invoice item must belong to its request.",
                )
            if quote:
                check(
                    item and quote.item_id == item.id,
                    "Quotation and invoice item do not match.",
                )
            if po:
                check(
                    item and (po.item_id or po.quote_line.item_id) == item.id,
                    "PO and invoice item do not match.",
                )
            if po:
                check(
                    price == po.unit_price,
                    "Invoice price must match the agreed PO price.",
                )
            if quote:
                check(
                    price == quote.unit_price,
                    "Invoice price must match its quotation source.",
                )
            line_net = amount(qty * price - discount, record.currency)
            policy = row.get("tax_policy")
            line_tax = Decimal(0)
            if policy:
                check(
                    policy.effective_from <= record.date
                    and (not policy.effective_to or record.date <= policy.effective_to),
                    "Tax policy is not effective on invoice date.",
                )
                check(
                    policy.verified_reference.strip(),
                    "Tax policy has no verification reference.",
                )
                line_tax = amount(line_net * policy.rate / 100, record.currency)
            obj = make(
                m.InvoiceLine,
                user,
                invoice=invoice,
                description=row["description"],
                unit=row.get("unit", "unit"),
                quantity=qty,
                unit_price=price,
                discount=discount,
                net=line_net,
                tax=line_tax,
                total=line_net + line_tax,
                item=item,
                quote_line=quote,
                tax_policy=policy,
            )
            if po:
                make(
                    m.InvoiceOrderLink,
                    user,
                    line=obj,
                    po_line=po,
                    quantity=qty,
                    evidence="Existing client purchase order",
                )
            net += line_net
            tax += line_tax
        total = net + tax
    elif kind in {"RECEIPT", "PAYMENT"}:
        total = amount(data["amount"], record.currency)
        check(total > 0, "Amount must be positive.")
        cash = data["cash_account"]
        check(
            cash.active and cash.account.currency_id == record.currency_id,
            "Select an active cash account in this currency.",
        )
        check(record.evidence_id is not None, "Payment/receipt evidence is required.")
        target = get_record(user, data["target"].pk) if data.get("target") else None
        if target:
            check(
                target.currency_id == record.currency_id,
                "Target currency does not match.",
            )
        purpose = data["purpose"]
        if kind == "RECEIPT":
            check(
                purpose in {"CLIENT", "ADVANCE_RETURN", "SUPPLIER_REFUND"},
                "Invalid receipt purpose.",
            )
            if purpose != "CLIENT":
                check(
                    target and target.status == "POSTED",
                    "Select a posted advance or supplier bill.",
                )
                check(
                    target.kind
                    == ("ADVANCE" if purpose == "ADVANCE_RETURN" else "SUPPLIER_BILL"),
                    "Receipt purpose does not match target.",
                )
            else:
                check(target is None, "Client receipts are allocated separately.")
            if purpose == "SUPPLIER_REFUND":
                check(
                    target.party_id == record.party_id,
                    "Refund supplier must match the paid bill.",
                )
            make(
                m.Receipt,
                user,
                record=record,
                payment_reference=data.get("reference", ""),
                cash_account=cash,
                purpose=purpose,
                target=target,
            )
        else:
            expected = {
                "BILL": "SUPPLIER_BILL",
                "EXPENSE": "EXPENSE",
                "ADVANCE": "ADVANCE",
                "TAX_REMITTANCE": "TAX_REMITTANCE",
            }
            check(purpose in {*expected, "CLIENT_REFUND"}, "Invalid payment purpose.")
            if purpose == "CLIENT_REFUND":
                check(
                    record.party_id and target is None,
                    "Client refund requires a client and no obligation target.",
                )
            else:
                check(
                    target
                    and target.kind == expected[purpose]
                    and target.status == "POSTED",
                    "Select the corresponding posted authorization/obligation.",
                )
                check(
                    target.party_id == record.party_id,
                    "Payment party does not match its target.",
                )
            make(
                m.Payment,
                user,
                record=record,
                cash_account=cash,
                purpose=purpose,
                target=target,
            )
    elif kind in {"EXPENSE", "SUPPLIER_BILL", "CLEARANCE"}:
        check(data.get("cost_lines"), "At least one actual cost is required.")
        for row in data["cost_lines"]:
            account = row["account"]
            check(
                account.active and account.classification in {"EXPENSE", "ASSET"},
                "Select an expense or asset cost account.",
            )
            value = amount(row["amount"], record.currency)
            check(value > 0, "Cost must be positive.")
            doc = evidence(user, row["evidence"], record.party)
            source = row.get("procurement_cost")
            if source:
                from procurement.services import visible_requests

                check(
                    visible_requests(user).filter(pk=source.item.request_id).exists(),
                    "Source cost is not accessible.",
                )
                check(
                    source.currency == record.currency.code
                    and amount(source.amount, record.currency) == value,
                    "Source cost amount/currency differs.",
                )
            make(
                m.CostLine,
                user,
                record=record,
                account=account,
                amount=value,
                description=row["description"],
                evidence=doc,
                procurement_cost=source,
            )
            total += value
        if kind == "CLEARANCE":
            target = get_record(user, data["advance"].pk)
            check(
                target.kind == "ADVANCE"
                and target.status == "POSTED"
                and target.currency_id == record.currency_id,
                "Select the posted advance in this currency.",
            )
            make(m.AdvanceClearance, user, record=record, advance=target.advance)
    elif kind == "ADVANCE":
        total = amount(data["amount"], record.currency)
        check(
            total > 0 and data["custodian"].is_active and data["custodian"].is_staff,
            "Select an active staff custodian and positive amount.",
        )
        check(record.description.strip(), "Advance purpose is required.")
        make(m.Advance, user, record=record, custodian=data["custodian"])
    elif kind == "CREDIT":
        original = get_record(user, data["invoice"].record_id)
        check(
            original.kind == "INVOICE"
            and original.status == "POSTED"
            and original.party_id == record.party_id
            and original.currency_id == record.currency_id,
            "Select the posted invoice for this client/currency.",
        )
        check(
            record.policy_id == original.policy_id,
            "Credit must use its invoice posting policy.",
        )
        check(record.description.strip(), "A cancellation reason is required.")
        credit = make(m.CreditNote, user, record=record, invoice=original.invoice)
        check(data.get("credit_lines"), "Select quantities to cancel.")
        seen = set()
        for row in data["credit_lines"]:
            line = row["line"]
            qty = amount(row["quantity"])
            check(
                line.invoice_id == original.invoice.pk
                and line.pk not in seen
                and 0 < qty <= line.quantity,
                "Invalid credit line/quantity.",
            )
            seen.add(line.pk)
            previous = m.CreditNoteLine.objects.filter(
                line=line, credit__record__status="POSTED"
            )
            credited_quantity = sum((r.quantity for r in previous), Decimal(0))
            final = credited_quantity + qty == line.quantity
            line_net = (
                line.net - sum((r.net for r in previous), Decimal(0))
                if final
                else amount(line.net * qty / line.quantity, record.currency)
            )
            line_tax = (
                line.tax - sum((r.tax for r in previous), Decimal(0))
                if final
                else amount(line.tax * qty / line.quantity, record.currency)
            )
            make(
                m.CreditNoteLine,
                user,
                credit=credit,
                line=line,
                quantity=qty,
                net=line_net,
                tax=line_tax,
                total=line_net + line_tax,
            )
            net += line_net
            tax += line_tax
        total = net + tax
    elif kind == "WITHHOLDING":
        line = data["line"]
        auth = data["authorization"]
        invoice = get_record(user, line.invoice.record_id)
        check(
            invoice.status == "POSTED"
            and invoice.party_id == record.party_id
            and invoice.currency_id == record.currency_id,
            "Withholding must reference this client/currency posted invoice.",
        )
        check(
            record.policy_id == invoice.policy_id,
            "Withholding must use its invoice posting policy.",
        )
        check(
            auth.party_id == record.party_id
            and (
                auth.treatment == "INCOME_WHT"
                or line.tax_policy_id
                and auth.tax_type_id == line.tax_policy.tax_type_id
            ),
            "Authorization and invoice tax type/client differ.",
        )
        check(
            record.evidence_id is not None and data["certificate_reference"].strip(),
            "Documented deduction reference/evidence is required.",
        )
        total = amount(data["amount"], record.currency)
        check(total > 0, "Deduction must be positive.")
        withholding_base = (
            amount(data.get("taxable_base", line.net), record.currency)
            if auth.treatment == "INCOME_WHT"
            else amount(0)
        )
        if auth.treatment == "INCOME_WHT":
            check(
                0 < withholding_base <= line.net,
                "Withholding base must be within the line net value, excluding VAT.",
            )
            check(
                total == amount(withholding_base * auth.rate / 100, record.currency),
                "Deduction must equal the approved client rate applied to the net base.",
            )
        make(
            m.WithholdingRecord,
            user,
            record=record,
            line=line,
            authorization=auth,
            rate=auth.rate if auth.treatment == "INCOME_WHT" else None,
            taxable_base=withholding_base,
            certificate_reference=data["certificate_reference"],
        )
    elif kind == "TAX_REMITTANCE":
        check(
            data["period_from"] <= data["period_to"] <= record.date,
            "Invalid remittance period.",
        )
        total = amount(data["amount"], record.currency)
        check(total > 0, "Remittance amount must be positive.")
        check(
            record.evidence_id is not None,
            "Remittance authorization evidence is required.",
        )
        make(
            m.TaxRemittance,
            user,
            record=record,
            tax_policy=data["tax_policy"],
            period_from=data["period_from"],
            period_to=data["period_to"],
        )
    elif kind in {"MANUAL", "OPENING"}:
        require(user, "add_journalentry" if kind == "MANUAL" else "add_openingbalance")
        check(data.get("journal_lines"), "Journal lines are required.")
        debit = Decimal(0)
        credit = Decimal(0)
        for row in data["journal_lines"]:
            check(
                row["account"].currency_id == record.currency_id,
                "Journal currency differs.",
            )
            dr = amount(row.get("debit", 0), record.currency)
            cr = amount(row.get("credit", 0), record.currency)
            check(
                (dr > 0 and cr == 0) or (cr > 0 and dr == 0),
                "Each line needs exactly one side.",
            )
            make(
                m.ManualLine,
                user,
                record=record,
                account=row["account"],
                debit=dr,
                credit=cr,
                party=row.get("party"),
            )
            debit += dr
            credit += cr
        check(debit == credit and debit > 0, "Journal must balance.")
        total = debit
    check(total > 0, "Financial record total must be positive.")
    record.amount = total
    record.net_amount = net
    record.tax_amount = tax
    record.save(update_fields=["amount", "net_amount", "tax_amount"])
    event(user, "created", record)
    return record


def snapshot(record):
    data = {
        f: getattr(record, f)
        for f in [
            "id",
            "kind",
            "party_id",
            "party_name",
            "currency_id",
            "policy_id",
            "date",
            "description",
            "reference",
            "amount",
            "net_amount",
            "tax_amount",
            "version",
            "evidence_id",
        ]
    }
    for relation in [
        "invoice",
        "receipt",
        "payment",
        "advance",
        "clearance",
        "credit_note",
        "withholding",
        "tax_remittance",
    ]:
        obj = getattr(record, relation, None)
        if obj is not None:
            data[relation] = {
                f.attname: getattr(obj, f.attname) for f in obj._meta.concrete_fields
            }
            if hasattr(obj, "lines"):
                data[relation]["lines"] = list(obj.lines.values())
    data["cost_lines"] = list(record.cost_lines.values())
    data["manual_lines"] = list(record.manual_lines.values())
    return primitive(data)


@transaction.atomic
def edit(user, record, expected, data):
    record = get_record(user, record.pk, lock=True, action="change")
    version(record, expected)
    check(record.status == "DRAFT", "Only drafts may be edited.")
    for field in ["description", "reference"]:
        if field in data:
            setattr(record, field, data[field])
    record.version += 1
    record.save(update_fields=["description", "reference", "version"])
    event(user, "draft_updated", record)
    return record


@transaction.atomic
def submit(user, record, expected):
    record = get_record(user, record.pk, lock=True, action="change")
    version(record, expected)
    check(record.status == "DRAFT", "Only drafts may be submitted.")
    data = snapshot(record)
    revision = make(
        m.FinancialRevision,
        user,
        record=record,
        version=record.version,
        snapshot=data,
        fingerprint=digest(data),
    )
    approval = make(m.ApprovalRequest, user, revision=revision, snapshot=data)
    record.status = "SUBMITTED"
    record.save(update_fields=["status"])
    queue_intent(user, approval=approval, event="SUBMITTED")
    event(user, "submitted", record, approval=approval.pk)
    return record


@transaction.atomic
def decide(user, record, expected, approved, reason):
    record = get_record(user, record.pk, lock=True, action="approve")
    version(record, expected)
    check(record.status == "SUBMITTED", "Record is not awaiting approval.")
    check(reason.strip(), "Record the approval/rejection reason.")
    approval = m.ApprovalRequest.objects.select_for_update().get(
        revision__record=record, revision__version=record.version
    )
    check(
        approval.revision.fingerprint == digest(snapshot(record)),
        "Submitted content has changed.",
    )
    if not record.policy.self_approval:
        check(record.created_by_id != user.pk, "A different approver is required.")
    make(m.ApprovalDecision, user, request=approval, approved=approved, reason=reason)
    approval.status = "APPROVED" if approved else "REJECTED"
    approval.save(update_fields=["status"])
    record.status = approval.status
    record.approved_by = user
    record.save(update_fields=["status", "approved_by"])
    queue_intent(user, approval=approval, event=approval.status)
    event(user, "approved" if approved else "rejected", record, reason)
    return record


def queue_intent(user, **kwargs):
    from ledger.integrations.workflow import schedule

    intent = make(m.IntegrationIntent, user, **kwargs)
    schedule(intent)
    return intent


@transaction.atomic
def revise(user, original, expected, reason, data):
    original = get_record(user, original.pk, lock=True, action="change")
    version(original, expected)
    check(
        original.status in {"DRAFT", "SUBMITTED", "APPROVED", "REJECTED"},
        "Posted records require a credit or reversal, not a revised draft.",
    )
    check(reason.strip(), "Revision reason is required.")
    replacement = create(user, original.kind, data)
    replacement.supersedes = original
    replacement.version = original.version + 1
    replacement.save(update_fields=["supersedes", "version"])
    original.status = "SUPERSEDED"
    original.save(update_fields=["status"])
    for approval in m.ApprovalRequest.objects.filter(revision__record=original).exclude(
        status="SUPERSEDED"
    ):
        approval.status = "SUPERSEDED"
        approval.save(update_fields=["status"])
        queue_intent(user, approval=approval, event="SUPERSEDED")
    event(user, "superseded", original, reason, replacement=replacement.pk)
    event(user, "revised", replacement, reason, previous=original.pk)
    return replacement
