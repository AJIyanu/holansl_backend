"""Explicit, repeatable historical linking. Never modifies cash or journals."""

from django.db import transaction
from django.db.models import Q
from .models import (
    SalesReceipt,
    PurchasePayment,
    SalesInvoice,
    PurchaseBill,
    TaxEntry,
    CashBookJournal,
)
from .tax_posting import payment_tax, totals, ZERO, lock_currency


def preview():
    missing = []
    deliveries = []
    discrepancies = []
    for model, side in [(SalesReceipt, "sales"), (PurchasePayment, "purchase")]:
        for p in model.objects.all().iterator():
            if not TaxEntry.objects.filter(source_key=f"payment:{p.pk}").exists():
                mismatch = payment_discrepancy(p, side)
                if mismatch:
                    discrepancies.append(
                        {"reference": p.transaction.reference, "reason": mismatch}
                    )
                missing.append(
                    {
                        "id": str(p.pk),
                        "side": side,
                        "reference": p.transaction.reference,
                        "vat": str(p.vat),
                        "wht": str(p.withholding),
                    }
                )
    for model, side, field in [
        (SalesInvoice, "sales", "posted_transaction"),
        (PurchaseBill, "purchase", "transaction"),
    ]:
        for doc in (
            model.objects.filter(**{f"{field}__isnull": False})
            .select_related(field)
            .iterator()
        ):
            txn = getattr(doc, field)
            if hasattr(txn, "reversal"):
                continue
            if not TaxEntry.objects.filter(source_key=f"delivery:{txn.pk}").exists():
                key = f"{side}:{doc.pk}"
                t = totals(TaxEntry.objects.filter(document_key=key))
                from .tax_posting import remaining_delivery_vat

                expected = remaining_delivery_vat(doc, side)
                recognised = (
                    t["output_vat"]
                    if side == "sales"
                    else t["input_vat"] + t["input_pending"] + t["input_nonrecoverable"]
                )
                deliveries.append(
                    {
                        "document_key": key,
                        "reference": txn.reference,
                        "currency": txn.currency,
                        "date": str(txn.date),
                        "expected_vat": str(expected),
                        "recorded_vat": str(recognised),
                        "recognition_gap": str(max(ZERO, expected - recognised)),
                    }
                )
    return {
        "discrepancies": discrepancies,
        "missing_payments": missing,
        "delivery_review": deliveries,
        "note": "Apply links first, then review delivery VAT openings. Preview never changes financial records.",
    }


@transaction.atomic
def apply_links():
    from crm.models import Party

    count = 0
    # Sorted party locks match application posting and keep concurrent backfills ordered.
    parties = list(Party.objects.order_by("pk").select_for_update())
    for currency in ["GBP", "NGN", "USD"]:
        lock_currency(currency)
    for party in parties:
        for model, side in [(SalesReceipt, "sales"), (PurchasePayment, "purchase")]:
            for p in (
                model.objects.filter(party=party)
                .select_related("transaction", "invoice" if side == "sales" else "bill")
                .order_by("date", "created_at", "id")
            ):
                if not TaxEntry.objects.filter(source_key=f"payment:{p.pk}").exists():
                    mismatch = payment_discrepancy(p, side)
                    if mismatch:
                        from rest_framework.exceptions import ValidationError

                        raise ValidationError(
                            f"{p.transaction.reference}: {mismatch}. No historical links applied."
                        )
                    payment_tax(p, side, historical=True)
                    count += 1
                root = TaxEntry.objects.get(source_key=f"payment:{p.pk}")
                for adjustment in p.transaction.adjustments.filter(
                    transaction_type__in=["REFUND", "REVERSAL"]
                ).order_by("date", "created_at", "id"):
                    key = f"correction:{adjustment.pk}"
                    if TaxEntry.objects.filter(source_key=key).exists():
                        continue
                    journals = CashBookJournal.objects.filter(
                        entry=adjustment.cash_entry
                    )
                    vat_account = "VAT_PAYABLE" if side == "sales" else "INPUT_TAX"
                    wht_account = "WHT_RECEIVABLE" if side == "sales" else "WHT_PAYABLE"

                    def movement(account, debit_positive):
                        return sum(
                            (
                                (
                                    j.amount
                                    if (j.debit_account == account) == debit_positive
                                    else -j.amount
                                )
                                for j in journals.filter(
                                    Q(debit_account=account) | Q(credit_account=account)
                                )
                            ),
                            ZERO,
                        )

                    vat = movement(vat_account, side == "purchase")
                    wht = movement(wht_account, side == "sales")
                    TaxEntry.objects.create(
                        source_key=key,
                        document_key=root.document_key,
                        kind="CORRECTION",
                        side=side,
                        date=adjustment.date,
                        period=adjustment.date.strftime("%Y-%m"),
                        currency=p.currency,
                        party=party,
                        transaction=adjustment,
                        cash_entry=adjustment.cash_entry,
                        reference=adjustment.reference,
                        description=adjustment.description,
                        actor=adjustment.created_by,
                        metadata={"historical": True},
                        **{
                            ("output_vat" if side == "sales" else "input_vat"): vat,
                            (
                                "sales_vat_cash"
                                if side == "sales"
                                else "purchase_vat_cash"
                            ): vat,
                            ("wht_asset" if side == "sales" else "wht_liability"): wht,
                        },
                    )
                    count += 1
    return count


def payment_discrepancy(payment, side):
    journals = list(
        CashBookJournal.objects.filter(entry=payment.transaction.cash_entry)
    )
    for account, expected, debit_positive in [
        (
            "VAT_PAYABLE" if side == "sales" else "INPUT_TAX",
            payment.vat,
            side == "purchase",
        ),
        (
            "WHT_RECEIVABLE" if side == "sales" else "WHT_PAYABLE",
            payment.withholding,
            side == "sales",
        ),
    ]:
        actual = sum(
            (
                j.amount
                if (j.debit_account == account) == debit_positive
                else -j.amount
                for j in journals
                if account in [j.debit_account, j.credit_account]
            ),
            ZERO,
        )
        if actual != expected:
            return "Stored payment tax does not match its journal; accountant review required"
    return ""
