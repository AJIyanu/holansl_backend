"""Keep receipt corrections consistent with actual cash and the tax journal legs."""

from decimal import Decimal, ROUND_HALF_UP, localcontext
from django.db.models import Sum
from .models import SalesReceipt, PurchasePayment, CashBookJournal


def refund_split(source, goods_amount):
    receipt = (
        (SalesReceipt if source.ledger_kind == "sales" else PurchasePayment)
        .objects.filter(transaction=source)
        .first()
    )
    if not receipt:
        return Decimal("0"), Decimal("0")
    prior = source.adjustments.filter(transaction_type="REFUND").aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0")

    # Cumulative rounding returns every last cent on a final full refund.
    def portion(total):
        def to_date(value):
            with localcontext() as ctx:
                ctx.prec = 40
                return (total * value / source.amount).quantize(
                    Decimal(".01"), rounding=ROUND_HALF_UP
                )

        return to_date(prior + goods_amount) - to_date(prior)

    return portion(receipt.vat), portion(receipt.withholding)


def correction_journals(*, record, source, action, vat, withholding, actor):
    common = dict(
        entry=record.cash_entry,
        currency=record.currency,
        date=record.date,
        description=record.description,
        reason=record.reason,
        reference=source.reference,
        actor=actor,
    )
    if action == "REVERSE":
        for row in source.cash_entry.journal.all():
            CashBookJournal.objects.create(
                **common,
                party_transaction=record
                if row.party_transaction_id == source.pk
                else None,
                amount=row.amount,
                debit_account=row.credit_account,
                credit_account=row.debit_account,
                reverses=row,
            )
    else:
        sales = source.ledger_kind == "sales"
        primary = True
        for value, debit, credit in [
            (
                record.amount - withholding,
                "RECEIVABLE" if sales else "CASH",
                "CASH" if sales else "PAYABLE",
            ),
            (
                withholding,
                "RECEIVABLE" if sales else "WHT_PAYABLE",
                "WHT_RECEIVABLE" if sales else "PAYABLE",
            ),
            (vat, "VAT_PAYABLE" if sales else "CASH", "CASH" if sales else "INPUT_TAX"),
        ]:
            if value:
                CashBookJournal.objects.create(
                    **common,
                    party_transaction=record if primary else None,
                    amount=value,
                    debit_account=debit,
                    credit_account=credit,
                )
                primary = False
