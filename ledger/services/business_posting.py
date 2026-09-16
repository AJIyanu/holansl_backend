"""Source-specific journals and protected invoice cancellation rules."""

from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from ledger import models as m
from .common import (
    amount,
    get_record,
    require,
    make,
    check,
    version,
    digest,
    Conflict,
    event,
    client_account,
)
from .posting import journal, lock_period
from .records import snapshot
from .settlement import (
    book_lock,
    movement,
    source_for,
    allocate,
    invoice_due,
    source_available,
    tax_withheld,
    tax_collected,
    credited,
)


def total(qs, field="amount"):
    return qs.aggregate(n=Sum(field))["n"] or Decimal(0)


def advance_outstanding(record):
    issued = total(
        m.Payment.objects.filter(target=record, record__status="POSTED"),
        "record__amount",
    )
    returns = total(
        m.Receipt.objects.filter(
            target=record, purpose="ADVANCE_RETURN", record__status="POSTED"
        ),
        "record__amount",
    )
    costs = total(
        m.AdvanceClearance.objects.filter(
            advance__record=record, record__status="POSTED"
        ),
        "record__amount",
    )
    return issued - costs - returns


def guard_credit(user, credit):
    get_record(user, credit.invoice.record_id)
    for row in credit.lines.select_related("line"):
        line = row.line
        previous = total(
            m.CreditNoteLine.objects.filter(line=line, credit__record__status="POSTED"),
            "quantity",
        )
        check(
            row.quantity <= line.quantity - previous, "Quantity was already credited."
        )
        previous_rows = m.CreditNoteLine.objects.filter(
            line=line, credit__record__status="POSTED"
        )
        remaining_net = line.net - total(previous_rows, "net")
        remaining_tax = line.tax - total(previous_rows, "tax")
        check(
            row.net <= remaining_net and row.tax <= remaining_tax,
            "Credit exceeds the remaining invoice value; revise the draft.",
        )
        if row.quantity == line.quantity - previous:
            check(
                row.net == remaining_net and row.tax == remaining_tax,
                "Final credit must include the remaining rounding amount; revise the draft.",
            )
        # Acceptance across all replacement attempts is cumulative; never cancel
        # accepted goods or treat missing outcome evidence as rejection.
        links = list(line.order_links.values_list("po_line_id", flat=True))
        if not links and line.item_id:
            from procurement.models import ClientPurchaseOrderLine

            links = list(
                ClientPurchaseOrderLine.objects.filter(
                    item_id=line.item_id
                ).values_list("pk", flat=True)
            )
        from procurement import models as pm

        deliveries = pm.DeliveryLine.objects.filter(
            receipt_line__purchase_line__attempt__po_line_id__in=links,
            delivery__dispatched_at__isnull=False,
        )
        dispatched = total(deliveries, "quantity")
        outcomes = pm.DeliveryOutcomeLine.objects.filter(delivery_line__in=deliveries)
        accepted = total(outcomes, "accepted")
        rejected = total(outcomes, "rejected")
        unresolved = max(dispatched - accepted - rejected, Decimal(0))
        check(
            row.quantity
            <= max(line.quantity - accepted - unresolved - previous, Decimal(0)),
            "Accepted or delivered-without-outcome quantities cannot be cancelled.",
        )
        check(
            not m.RevenueRecognition.objects.filter(line=line).exists()
            or row.quantity
            <= line.quantity - total(line.recognitions.all(), "quantity") - previous,
            "Recognised accepted quantities cannot be cancelled.",
        )
        check(
            row.tax
            <= line.tax - tax_withheld(line) - tax_collected(line) - credited(line),
            "Resolve collected/withheld tax before crediting this tax component.",
        )


@transaction.atomic
def post(user, record, expected):
    record = get_record(user, record.pk, action="post")
    # Serialize native-currency mutations; this deliberately favours correctness
    # over concurrent writers inside one currency in the staff dashboard.
    request_ids = {record.request_id}
    if record.kind == "CREDIT":
        request_ids.add(record.credit_note.invoice.record.request_id)
    if record.kind == "WITHHOLDING":
        request_ids.add(record.withholding.line.invoice.record.request_id)
    from procurement.models import ProcurementRequest

    list(
        ProcurementRequest.objects.select_for_update()
        .filter(pk__in=[pk for pk in request_ids if pk])
        .order_by("pk")
    )
    book_lock(record.currency)
    lock_period(record.date)
    if record.party_id:
        client_account(user, record.party, record.currency, lock=True)
    record = get_record(user, record.pk, lock=True, action="post")
    version(record, expected)
    check(record.status == "APPROVED", "Only approved records may be posted.")
    revision = record.revisions.get(version=record.version)
    check(
        revision.fingerprint == digest(snapshot(record)),
        "Approved content changed; resubmit for approval.",
    )
    rows = []
    p = record.policy
    party = record.party
    value = record.amount
    kind = record.kind

    def row(account, dr=0, cr=0, staff=None):
        rows.append((account, dr, cr, party, staff))

    if kind == "INVOICE":
        from procurement.models import ClientPurchaseOrderLine

        for item in {
            line.item
            for line in record.invoice.lines.select_related("item")
            if line.item_id
        }:
            ordered = total(
                ClientPurchaseOrderLine.objects.filter(item=item), "quantity"
            )
            capacity = ordered or item.quantity
            previous = total(
                m.InvoiceLine.objects.filter(
                    item=item, invoice__record__status="POSTED"
                ),
                "quantity",
            )
            cancelled = total(
                m.CreditNoteLine.objects.filter(
                    line__item=item, credit__record__status="POSTED"
                ),
                "quantity",
            )
            current = total(record.invoice.lines.filter(item=item), "quantity")
            check(
                current <= capacity - previous + cancelled,
                "Invoice quantity exceeds the client commitment remaining to be invoiced.",
            )
        row(p.receivable, value)
        row(p.deferred_revenue, cr=record.net_amount)
        for line in record.invoice.lines.select_related(
            "tax_policy__liability_account"
        ):
            if line.tax:
                row(line.tax_policy.liability_account, cr=line.tax)
    elif kind == "RECEIPT":
        detail = record.receipt
        cash = detail.cash_account
        check(cash.active, "Cash account is inactive.")
        row(cash.account, value)
        if detail.purpose == "CLIENT":
            row(p.receivable, cr=value)
        elif detail.purpose == "ADVANCE_RETURN":
            check(
                value <= advance_outstanding(detail.target),
                "Return exceeds outstanding advance.",
            )
            row(
                detail.target.policy.advance,
                cr=value,
                staff=detail.target.advance.custodian,
            )
        else:
            target = detail.target
            paid = total(
                m.Payment.objects.filter(target=target, record__status="POSTED"),
                "record__amount",
            )
            refunded = total(
                m.Receipt.objects.filter(target=target, record__status="POSTED"),
                "record__amount",
            )
            check(
                value <= paid - refunded, "Refund exceeds verified supplier payments."
            )
            row(target.policy.payable, cr=value)
    elif kind in {"EXPENSE", "SUPPLIER_BILL", "CLEARANCE"}:
        for cost in record.cost_lines.select_related("account"):
            if cost.procurement_cost_id:
                check(
                    not m.CostLine.objects.filter(
                        procurement_cost_id=cost.procurement_cost_id,
                        record__status="POSTED",
                    )
                    .exclude(record=record)
                    .exists(),
                    "This procurement cost was already posted.",
                )
            check(
                cost.account
                not in {p.receivable, p.advance, p.opening_equity, p.deferred_revenue},
                "Control accounts cannot be posted as ordinary costs.",
            )
            check(
                not m.CashAccount.objects.filter(
                    account__account=cost.account
                ).exists(),
                "Bank/cash cannot be posted as an ordinary cost.",
            )
            row(cost.account, cost.amount)
        if kind == "CLEARANCE":
            target = record.clearance.advance.record
            check(
                value <= advance_outstanding(target),
                "Clearance exceeds the unaccounted advance.",
            )
            row(
                target.policy.advance,
                cr=value,
                staff=record.clearance.advance.custodian,
            )
        else:
            row(p.payable, cr=value)
    elif kind == "PAYMENT":
        detail = record.payment
        target = detail.target
        check(detail.cash_account.active, "Cash account is inactive.")
        if target:
            target = get_record(user, target.pk)
            check(target.status == "POSTED", "Payment target is no longer posted.")
            spent = total(
                m.Payment.objects.filter(target=target, record__status="POSTED"),
                "record__amount",
            )
            check(
                value <= target.amount - spent,
                "Payment exceeds remaining approved obligation.",
            )
        if detail.purpose in {"BILL", "EXPENSE"}:
            row(target.policy.payable, value)
        elif detail.purpose == "ADVANCE":
            row(target.policy.advance, value, staff=target.advance.custodian)
        elif detail.purpose == "CLIENT_REFUND":
            account = client_account(user, party, record.currency, lock=True)
            balance = total(account.movements.all(), "debit") - total(
                account.movements.all(), "credit"
            )
            check(value <= -balance, "Refund exceeds client credit held.")
            sources = m.SettlementSource.objects.filter(
                account=account, record__status="POSTED"
            ).order_by("created_at", "id")
            # Refund consumes available client credits through typed refund applications.
            remaining = value
            for source in sources:
                available = source_available(source)
                part = min(available, remaining)
                if part > 0:
                    make(
                        m.RefundApplication,
                        user,
                        payment=detail,
                        source=source,
                        amount=part,
                    )
                    remaining -= part
            check(
                remaining == 0,
                "Release invoice allocations before refunding that money.",
            )
            row(p.receivable, value)
        else:
            from .tax import remittable

            policy = target.tax_remittance.tax_policy
            check(
                value <= remittable(policy, record.currency),
                "Payment exceeds tax collected and not yet remitted.",
            )
            row(policy.liability_account, value)
        row(detail.cash_account.account, cr=value)
    elif kind == "WITHHOLDING":
        detail = record.withholding
        line = detail.line
        auth = m.ClientWithholdingAuthorization.objects.select_for_update().get(
            pk=detail.authorization_id
        )
        check(
            auth.effective_from <= record.date
            and (not auth.effective_to or record.date <= auth.effective_to)
            and (not auth.revoked_from or record.date < auth.revoked_from),
            "Client was not authorized on the deduction date.",
        )
        check(
            value <= invoice_due(line.invoice), "Deduction exceeds invoice outstanding."
        )
        policy = line.tax_policy
        if auth.treatment == "INCOME_WHT":
            used = sum(
                (
                    r.taxable_base
                    for r in line.withholdings.filter(
                        record__status="POSTED", authorization__treatment="INCOME_WHT"
                    )
                ),
                Decimal(0),
            )
            credited_net = sum(
                (
                    r.net
                    for r in m.CreditNoteLine.objects.filter(
                        line=line, credit__record__status="POSTED"
                    )
                ),
                Decimal(0),
            )
            check(
                detail.rate == auth.rate
                and value
                == amount(detail.taxable_base * detail.rate / 100, record.currency),
                "Withholding authorization changed; revise the draft.",
            )
            check(
                used + detail.taxable_base <= line.net - credited_net,
                "Withholding base exceeds remaining net goods value.",
            )
            check(
                auth.credit_account_id
                and auth.credit_account.active
                and auth.credit_account.classification == "ASSET",
                "Tax credit asset account is not configured.",
            )
            row(auth.credit_account, value)
        elif policy.treatment == "CLIENT_REMITTANCE":
            check(
                value
                <= line.tax - tax_collected(line) - tax_withheld(line) - credited(line),
                "Withholding exceeds remaining invoice tax.",
            )
            row(policy.liability_account, value)
        else:
            check(policy.withheld_account_id, "Tax credit account is not configured.")
            row(policy.withheld_account, value)
        row(p.receivable, cr=value)
    elif kind == "CREDIT":
        require(user, "cancel_invoice")
        guard_credit(user, record.credit_note)
        for credit in record.credit_note.lines.select_related("line__tax_policy"):
            row(p.deferred_revenue, credit.net)
            if credit.tax:
                row(credit.line.tax_policy.liability_account, credit.tax)
        row(p.receivable, cr=value)
    elif kind in {"MANUAL", "OPENING"}:
        protected = set()
        for pol in m.PostingPolicyVersion.objects.all():
            protected.update(
                [
                    pol.receivable_id,
                    pol.payable_id,
                    pol.advance_id,
                    pol.deferred_revenue_id,
                ]
            )
        protected.update(
            m.TaxPolicyVersion.objects.values_list("liability_account_id", flat=True)
        )
        for line in record.manual_lines.select_related("account__account", "party"):
            if kind == "MANUAL":
                check(
                    line.account.account_id not in protected
                    and not line.account.account.purpose,
                    "Use the owning workflow for control accounts.",
                )
                check(
                    line.party_id is None,
                    "Manual journals cannot alter a client subledger.",
                )
            else:
                check(
                    not m.JournalEntry.objects.filter(
                        currency=record.currency, posted_at__isnull=False
                    ).exists(),
                    "Opening balances must precede operational posting in this currency.",
                )
                check(
                    line.account.account_id
                    not in {p.payable_id, p.advance_id, p.deferred_revenue_id},
                    "Detailed supplier/advance opening imports are required for these control accounts.",
                )
                check(
                    line.account.account_id != p.receivable_id
                    or line.party_id is not None,
                    "Client opening balance requires a client on each receivable line.",
                )
            rows.append((line.account, line.debit, line.credit, line.party, None))
    elif kind not in {"ADVANCE", "TAX_REMITTANCE"}:
        raise Conflict("Unsupported posting kind.")
    entry = journal(user, record, "POST", rows) if rows else None
    record.status = "POSTED"
    record.posted_at = timezone.now()
    record.save(update_fields=["status", "posted_at"])
    if kind == "INVOICE":
        movement(user, record, entry, "INVOICE", debit=value)
        from ledger.integrations.procurement import register_financial

        register_financial(user, record, "IN")
    elif kind in {"RECEIPT", "WITHHOLDING", "CREDIT"} and (
        kind != "RECEIPT" or record.receipt.purpose == "CLIENT"
    ):
        account = movement(user, record, entry, kind, credit=value)
        source = source_for(user, record, account)
        if kind == "RECEIPT":
            from ledger.integrations.procurement import register_financial

            register_financial(user, record, "RC")
        else:
            invoice = (
                record.withholding.line.invoice
                if kind == "WITHHOLDING"
                else record.credit_note.invoice
            )
            # A paid invoice's credit remains client credit instead of disappearing.
            due = invoice_due(invoice)
            if due > 0:
                allocate(
                    user, source, invoice, min(value, due), record.date, internal=True
                )
    elif kind == "PAYMENT" and record.payment.purpose == "CLIENT_REFUND":
        movement(user, record, entry, "REFUND", debit=value)
    elif kind == "OPENING":
        for party_id in (
            record.manual_lines.exclude(party=None)
            .values_list("party_id", flat=True)
            .distinct()
        ):
            from crm.models import Party

            account = client_account(
                user, Party.objects.get(pk=party_id), record.currency, lock=True
            )
            qs = record.manual_lines.filter(
                party_id=party_id, account__account=p.receivable
            )
            balance = total(qs, "debit") - total(qs, "credit")
            if balance:
                make(
                    m.ClientMovement,
                    user,
                    account=account,
                    source=record,
                    journal=entry,
                    date=record.date,
                    kind="OPENING",
                    debit=max(balance, 0),
                    credit=max(-balance, 0),
                )
    event(user, "posted", record, journal=entry.pk if entry else None)
    return record
