"""Payment/delivery VAT recognition with append-only, source-keyed tax movements."""

from decimal import Decimal, ROUND_HALF_UP, localcontext
from uuid import uuid5, NAMESPACE_URL
from django.db.models import Sum
from rest_framework.exceptions import ValidationError
from .models import TaxEntry, CashBookJournal, SalesReceipt, PurchasePayment, TaxPolicy
from .idempotency import lock_submission

ZERO = Decimal("0.00")
FIELDS = [
    "output_vat",
    "input_vat",
    "input_pending",
    "input_nonrecoverable",
    "sales_vat_cash",
    "purchase_vat_cash",
    "wht_asset",
    "wht_liability",
    "input_used",
    "vat_remitted",
    "wht_remitted",
    "cit_assessed",
    "cit_paid",
    "wht_used",
    "party_vat",
    "delivery_vat",
]
CLASS_ACCOUNT = {
    "AVAILABLE": "INPUT_TAX",
    "PENDING": "INPUT_PENDING",
    "NONRECOVERABLE": "EXPENSE",
}
CLASS_FIELD = {
    "AVAILABLE": "input_vat",
    "PENDING": "input_pending",
    "NONRECOVERABLE": "input_nonrecoverable",
}


def lock_currency(currency):
    lock_submission(uuid5(NAMESPACE_URL, f"holan:tax-ledger:{currency}"))


def totals(rows):
    return {
        k: v or ZERO for k, v in rows.aggregate(**{f: Sum(f) for f in FIELDS}).items()
    }


def policy_rate(kind, date, party_id=None):
    rows = TaxPolicy.objects.filter(kind=kind, effective_date__lte=date)
    specific = rows.filter(party_id=party_id).first() if party_id else None
    policy = specific or rows.filter(party__isnull=True).first()
    if policy:
        return policy.rate if policy.enabled else ZERO
    return Decimal("7.5") if kind == "VAT" else Decimal("2")


def rounded(value):
    with localcontext() as ctx:
        ctx.prec = 40
        return value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)


def journal(entry, amount, debit, credit, *, cash=None):
    if not amount:
        return
    if amount < 0:
        amount, debit, credit = -amount, credit, debit
    return CashBookJournal.objects.create(
        entry=cash,
        date=entry.date,
        currency=entry.currency,
        amount=amount,
        debit_account=debit,
        credit_account=credit,
        description=entry.description[:500],
        reference=entry.reference,
        reason=f"Tax ledger {entry.pk}",
        actor=entry.actor,
    )


def category(key):
    row = (
        TaxEntry.objects.filter(document_key=key, kind="CLASSIFY")
        .order_by("-created_at", "-id")
        .first()
    )
    return row.metadata["classification"] if row else "AVAILABLE"


def group_for(document, side):
    return f"{side}:{document.pk}"


def require_history(document, side):
    model = SalesReceipt if side == "sales" else PurchasePayment
    rows = model.objects.filter(**{"invoice" if side == "sales" else "bill": document})
    keys = [f"payment:{pk}" for pk in rows.values_list("pk", flat=True)]
    if keys and TaxEntry.objects.filter(source_key__in=keys).count() != len(keys):
        raise ValidationError(
            "Existing linked payments need the tax backfill before this operation. Run its preview and review the results."
        )


def validate_payment(document, side, subtotal, vat, date):
    if not document:
        return
    require_history(document, side)

    transaction_id = (
        document.posted_transaction_id if side == "sales" else document.transaction_id
    )
    if (
        transaction_id
        and not TaxEntry.objects.filter(
            source_key=f"delivery:{transaction_id}"
        ).exists()
    ):
        raise ValidationError(
            "Approve the historical delivery VAT opening before recording further linked payments."
        )
    model = SalesReceipt if side == "sales" else PurchasePayment
    payments = model.objects.filter(
        **{"invoice" if side == "sales" else "bill": document}
    )
    paid = ZERO
    for p in payments.select_related("transaction"):
        if hasattr(p.transaction, "reversal"):
            continue
        refunds = (
            p.transaction.adjustments.filter(transaction_type="REFUND").aggregate(
                total=Sum("amount")
            )["total"]
            or ZERO
        )
        paid += p.subtotal - refunds
    if paid + subtotal > document.subtotal:
        raise ValidationError(
            "This payment exceeds the linked document’s remaining goods value. Record an unrelated advance separately."
        )
    key = group_for(document, side)
    events = TaxEntry.objects.filter(document_key=key)
    last = events.order_by("-date").first()
    if last and date < last.date:
        raise ValidationError(
            "A linked tax payment cannot precede an existing tax event for the document."
        )
    # Cumulative caps preserve full tax on final settlement without double charging.
    expected = (
        Decimal(document.confirmed_revision.snapshot["vat"])
        if side == "sales" and document.confirmed_revision_id
        else (
            rounded(document.subtotal * document.tax_rate / 100)
            if side == "sales"
            else Decimal(document.snapshot["tax"])
        )
    )
    cash = totals(events)["sales_vat_cash" if side == "sales" else "purchase_vat_cash"]
    if cash + vat > expected:
        raise ValidationError(
            "VAT exceeds the linked document’s remaining VAT. Review the payment amount and rate."
        )


def post_event(
    *,
    source_key,
    key,
    side,
    txn,
    vat_cash=ZERO,
    wht=ZERO,
    delivery=ZERO,
    base_vat=ZERO,
    actor=None,
    kind="PAYMENT",
    metadata=None,
    historical=False,
):
    """base_vat is the signed VAT already posted by the original accounting path."""
    lock_currency(txn.currency)
    prior = TaxEntry.objects.filter(source_key=source_key).first()
    if prior:
        return prior
    rows = TaxEntry.objects.filter(document_key=key)
    before = totals(rows)
    latest = rows.order_by("-date").first()
    if latest and txn.date < latest.date and not historical:
        raise ValidationError(
            "Tax event date cannot precede an existing event for this document."
        )
    sales = side == "sales"
    recognised = (
        before["output_vat"]
        if sales
        else before["input_vat"]
        + before["input_pending"]
        + before["input_nonrecoverable"]
    )
    cash_total = before["sales_vat_cash" if sales else "purchase_vat_cash"] + vat_cash
    delivered_total = before["delivery_vat"] + delivery
    if cash_total < 0 or delivered_total < 0:
        raise ValidationError("Tax correction exceeds the document’s recorded amounts.")
    desired = max(cash_total, delivered_total)
    delta = base_vat if historical else desired - recognised
    # VAT debt exists only after delivery; advance receipts do not create negative debt.
    has_delivery = (
        delivered_total > 0
        or rows.filter(kind__in=["DELIVERY", "OPENING"]).exists()
        or kind == "DELIVERY"
    )
    desired_party = delivered_total - cash_total if has_delivery else ZERO
    party_delta = ZERO if historical else desired_party - before["party_vat"]
    c = category(key)
    values = {
        ("output_vat" if sales else CLASS_FIELD[c]): delta,
        ("sales_vat_cash" if sales else "purchase_vat_cash"): vat_cash,
        ("wht_asset" if sales else "wht_liability"): wht,
        "delivery_vat": delivery,
        "party_vat": party_delta,
    }
    entry = TaxEntry.objects.create(
        source_key=source_key,
        document_key=key,
        side=side,
        kind=kind,
        date=txn.date,
        period=txn.date.strftime("%Y-%m"),
        currency=txn.currency,
        party=txn.party,
        transaction=txn,
        cash_entry=txn.cash_entry,
        reference=txn.reference,
        description=txn.description,
        actor=actor or txn.created_by,
        metadata=metadata or {},
        **values,
    )
    if not historical:
        # Existing cash path posts all paid VAT. Reclassify the delivered portion to
        # the party control account; delivery itself posts only the unrecognised VAT.
        difference = delta - base_vat
        journal(
            entry,
            difference,
            "RECEIVABLE" if sales else "INPUT_TAX",
            "VAT_PAYABLE" if sales else "PAYABLE",
        )
        if not sales and c != "AVAILABLE":
            journal(entry, delta, CLASS_ACCOUNT[c], "INPUT_TAX")
    ensure_assets(txn.currency)
    return entry


def ensure_assets(currency):
    t = totals(TaxEntry.objects.filter(currency=currency))
    if (
        t["wht_asset"] < t["wht_used"]
        or t["wht_liability"] < t["wht_remitted"]
        or t["input_vat"] < t["input_used"]
    ):
        raise ValidationError(
            "This correction affects tax credits or liabilities already settled. Reverse the relevant tax settlement through an approved adjustment first."
        )


def payment_tax(payment, side, *, historical=False):
    document = payment.invoice if side == "sales" else payment.bill
    key = group_for(document, side) if document else f"{side}:payment:{payment.pk}"
    return post_event(
        source_key=f"payment:{payment.pk}",
        key=key,
        side=side,
        txn=payment.transaction,
        vat_cash=payment.vat,
        wht=payment.withholding,
        base_vat=payment.vat,
        historical=historical,
        metadata={
            "historical": historical,
            "goods": str(payment.subtotal),
            "vat": str(payment.vat),
            "withholding": str(payment.withholding),
            "rate": payment.snapshot.get(
                "vat_rate", payment.snapshot.get("tax_percent", "7.5")
            ),
        },
    )


def delivery_tax(document, side):
    require_history(document, side)
    txn = document.posted_transaction if side == "sales" else document.transaction
    vat = (
        Decimal(document.confirmed_revision.snapshot["vat"])
        if side == "sales"
        else Decimal(document.snapshot["tax"])
    )
    # A source key is created even for zero-VAT delivery, preserving event identity.
    return post_event(
        source_key=f"delivery:{txn.pk}",
        key=group_for(document, side),
        side=side,
        txn=txn,
        delivery=vat,
        kind="DELIVERY",
        metadata={"goods": str(document.subtotal), "vat": str(vat)},
    )


def correction_tax(record, source, action, vat=ZERO, withholding=ZERO):
    original = (
        TaxEntry.objects.filter(transaction=source).exclude(kind="CLASSIFY").first()
    )
    if not original:
        if hasattr(source, "receipt_document") or hasattr(source, "purchase_payment"):
            raise ValidationError(
                "Backfill this historical payment’s tax records before correcting it."
            )
        return
    if action in ["REFUND", "REVERSE"] and source.transaction_type == "PAYMENT":
        if action == "REVERSE":
            vat = abs(original.sales_vat_cash + original.purchase_vat_cash)
            withholding = abs(original.wht_asset + original.wht_liability)
        # Cash reversal reverses the original paid VAT; recognition is recalculated.
        base = -vat
        return post_event(
            source_key=f"correction:{record.pk}",
            key=original.document_key,
            side=source.ledger_kind,
            txn=record,
            vat_cash=-vat,
            wht=-withholding,
            base_vat=base,
            kind="CORRECTION",
        )
    if action in ["CREDIT", "REVERSE"] and source.transaction_type in [
        "INVOICE",
        "BILL",
    ]:
        original_vat = original.delivery_vat
        prior_goods = (
            source.adjustments.filter(transaction_type="CREDIT")
            .exclude(pk=record.pk)
            .aggregate(n=Sum("amount"))["n"]
            or ZERO
        )
        with localcontext() as ctx:
            ctx.prec = 40
            reduction = (
                original_vat
                if action == "REVERSE"
                else rounded(
                    original_vat * (prior_goods + record.amount) / source.amount
                )
                - rounded(original_vat * prior_goods / source.amount)
            )
        # Generic reversal only reverses the goods journal; VAT has its own pair.
        return post_event(
            source_key=f"correction:{record.pk}",
            key=original.document_key,
            side=source.ledger_kind,
            txn=record,
            delivery=-reduction,
            kind="CORRECTION",
        )


def validate_document_edit(document, side, subtotal, vat, currency):
    """A revised advance document must still cover money already acknowledged."""
    model = SalesReceipt if side == "sales" else PurchasePayment
    payments = model.objects.filter(
        **{"invoice" if side == "sales" else "bill": document}
    )
    if not payments.exists():
        return
    if currency != document.currency:
        raise ValidationError("A document with payments must retain its currency.")
    require_history(document, side)
    net = ZERO
    for p in payments.select_related("transaction"):
        if not hasattr(p.transaction, "reversal"):
            net += p.subtotal - (
                p.transaction.adjustments.filter(transaction_type="REFUND").aggregate(
                    n=Sum("amount")
                )["n"]
                or ZERO
            )
    paid_vat = totals(TaxEntry.objects.filter(document_key=group_for(document, side)))[
        "sales_vat_cash" if side == "sales" else "purchase_vat_cash"
    ]
    if subtotal < net or vat < paid_vat:
        raise ValidationError(
            "The revised goods value and VAT must cover payments already recorded."
        )


def remaining_delivery_vat(document, side):
    txn = document.posted_transaction if side == "sales" else document.transaction
    original = Decimal(
        document.confirmed_revision.snapshot["vat"]
        if side == "sales"
        else document.snapshot["tax"]
    )
    credits = (
        txn.adjustments.filter(transaction_type="CREDIT").aggregate(n=Sum("amount"))[
            "n"
        ]
        or ZERO
    )
    with localcontext() as ctx:
        ctx.prec = 40
        return original - rounded(original * credits / txn.amount)
