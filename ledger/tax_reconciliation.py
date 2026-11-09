from .models import TaxEntry
from .tax_posting import ZERO


def enrich(content, records, party_id, kind, start, end, currency):
    tax = list(
        TaxEntry.objects.filter(
            party_id=party_id, side=kind, currency=currency, date__lte=end
        )
        .select_related("cash_entry")
        .order_by("date", "created_at", "id")
    )
    opening = sum((e.party_vat for e in tax if e.date < start), ZERO)
    closing = sum((e.party_vat for e in tax), ZERO)
    by_txn = {}
    extras = []
    txns = {r.pk: r for r in records}
    for e in tax:
        if e.date < start:
            continue
        if e.transaction_id in txns and txns[e.transaction_id].date == e.date:
            by_txn.setdefault(str(e.transaction_id), []).append(e)
        else:
            extras.append(e)
    rows = content["transactions"]
    for row in rows:
        from uuid import UUID

        txn = txns[UUID(row["id"])]
        events = by_txn.get(row["id"], [])
        row.update(
            goods_debit=row["debit"],
            goods_credit=row["credit"],
            vat_recognised=str(
                sum(
                    (
                        e.output_vat
                        + e.input_vat
                        + e.input_pending
                        + e.input_nonrecoverable
                        for e in events
                    ),
                    ZERO,
                )
            ),
            vat_cash=str(
                sum((e.sales_vat_cash + e.purchase_vat_cash for e in events), ZERO)
            ),
            wht=str(sum((e.wht_asset + e.wht_liability for e in events), ZERO)),
            tax_balance_change=str(sum((e.party_vat for e in events), ZERO)),
            actual_cash=str(
                -txn.effect * txn.cash_entry.amount if txn.cash_entry_id else ZERO
            ),
            order=str(txn.created_at),
        )
    for e in extras:
        rows.append(
            {
                "id": str(e.pk),
                "date": str(e.date),
                "type": e.kind,
                "reference": e.reference,
                "description": e.description,
                "debit": "0.00",
                "credit": "0.00",
                "goods_debit": "0.00",
                "goods_credit": "0.00",
                "vat_recognised": str(
                    e.output_vat
                    + e.input_vat
                    + e.input_pending
                    + e.input_nonrecoverable
                ),
                "vat_cash": str(e.sales_vat_cash + e.purchase_vat_cash),
                "wht": str(e.wht_asset + e.wht_liability),
                "tax_balance_change": str(e.party_vat),
                "actual_cash": str(
                    e.cash_entry.amount
                    if e.kind == "VAT_SETTLE" and e.cash_entry
                    else ZERO
                ),
                "order": str(e.created_at),
            }
        )
    from decimal import Decimal

    goods = Decimal(content["opening_balance"])
    vat = opening
    for row in sorted(rows, key=lambda r: (r["date"], r["order"], r["id"])):
        goods += Decimal(row["goods_debit"]) - Decimal(row["goods_credit"])
        vat += Decimal(row["tax_balance_change"])
        row["goods_balance"] = str(goods)
        row["vat_balance"] = str(vat)
        row["combined_balance"] = str(goods + vat)
        row["balance"] = str(goods)
    content["transactions"] = sorted(
        rows, key=lambda r: (r["date"], r["order"], r["id"])
    )
    period = [e for e in tax if e.date >= start]
    content["tax"] = {
        "opening_vat": str(opening),
        "closing_vat": str(closing),
        "opening_total": str(Decimal(content["opening_balance"]) + opening),
        "closing_total": str(Decimal(content["closing_balance"]) + closing),
        "vat_recognised": str(
            sum(
                (
                    e.output_vat
                    + e.input_vat
                    + e.input_pending
                    + e.input_nonrecoverable
                    for e in period
                ),
                ZERO,
            )
        ),
        "vat_cash": str(
            sum((e.sales_vat_cash + e.purchase_vat_cash for e in period), ZERO)
        ),
        "wht": str(sum((e.wht_asset + e.wht_liability for e in period), ZERO)),
        "actual_cash": str(sum((Decimal(r["actual_cash"]) for r in rows), ZERO)),
    }
    return content
