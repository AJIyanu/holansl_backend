"""Supplier bills become debt on delivery; itemized payments post actual cash now."""

from decimal import Decimal, ROUND_HALF_UP, localcontext
from uuid import uuid4
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from crm.models import Party
from .models import (
    PurchaseBill,
    PurchaseBillEvent,
    PurchasePayment,
    PartyLedgerTransaction,
    LedgerAccount,
    ProcurementLedgerLink,
    LedgerOperation,
)
from .invoices import ItemInput, today, MAX_AMOUNT
from .operations import require
from .idempotency import lock_submission
from .party_posting import post_party_transaction
from .balances import remaining


def access(actor, resource, action):
    require(
        actor,
        "ledger.view_purchaseledger",
        f"ledger.view_{resource}",
        f"ledger.{action}_{resource}",
    )
    if resource == "purchasepayment" and action == "add":
        require(actor, "ledger.add_purchasetransaction", "ledger.add_cashbookentry")


class PurchaseInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    version = serializers.IntegerField(min_value=1, required=False)
    date = serializers.DateField()
    currency = serializers.ChoiceField(choices=["NGN", "USD", "GBP"])
    description = serializers.CharField(max_length=500, allow_blank=True, default="")
    items = ItemInput(many=True, min_length=1, max_length=100)
    tax_enabled = serializers.BooleanField(default=False)
    tax_percent = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=0,
        max_value=100,
        default=Decimal("7.5"),
    )
    discount_enabled = serializers.BooleanField(default=False)
    discount_type = serializers.ChoiceField(
        choices=["AMOUNT", "PERCENT"], default="PERCENT"
    )
    discount_value = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=0, default=Decimal("0")
    )

    def validate(self, attrs):
        if attrs["date"] > today():
            raise ValidationError("Choose a date on or before today.")
        price(attrs)
        return attrs


class BillInput(PurchaseInput):
    reference = serializers.CharField(max_length=100)
    delivered = serializers.BooleanField(default=False)
    delivery_date = serializers.DateField(required=False)


class PaymentInput(PurchaseInput):
    account_id = serializers.UUIDField()
    payment_method = serializers.ChoiceField(
        choices=["CASH", "BANK_TRANSFER", "CARD", "CHEQUE"]
    )
    bank_name = serializers.CharField(max_length=255, allow_blank=True, default="")
    receipt_number = serializers.CharField(max_length=100, allow_blank=True, default="")
    bill_id = serializers.UUIDField(required=False, allow_null=True)
    bill_version = serializers.IntegerField(min_value=1, required=False)
    withholding_enabled = serializers.BooleanField(default=False)
    withholding_percent = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=0,
        max_value=Decimal("99.99"),
        default=Decimal("2"),
    )


def price(d):
    def rounded(v):
        return v.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)

    with localcontext() as ctx:
        ctx.prec = 40
        gross = [rounded(row["quantity"] * row["unit_price"]) for row in d["items"]]
        total = sum(gross, Decimal("0"))
        if not 0 < total <= MAX_AMOUNT:
            raise ValidationError(
                "Item total must be positive and within the supported limit."
            )
        value = d["discount_value"] if d["discount_enabled"] else Decimal("0")
        if d["discount_type"] == "PERCENT" and value > 100:
            raise ValidationError("Percentage discount cannot exceed 100%.")
        discount = (
            rounded(total * value / 100) if d["discount_type"] == "PERCENT" else value
        )
        net = total - discount
        if net <= 0:
            raise ValidationError("Discount must leave a positive goods subtotal.")
        tax = (
            rounded(net * d["tax_percent"] / 100)
            if d["tax_enabled"]
            else Decimal("0.00")
        )
        withholding = (
            rounded(net * d.get("withholding_percent", Decimal("2")) / 100)
            if d.get("withholding_enabled")
            else Decimal("0.00")
        )
        paid = net + tax - withholding
        if not 0 < paid <= MAX_AMOUNT or withholding >= net:
            raise ValidationError(
                "Withholding must leave positive goods value and the cash amount must fit the supported limit."
            )
        cumulative, assigned = Decimal("0"), Decimal("0")
        items = []
        for row, amount in zip(d["items"], gross):
            cumulative += amount
            next_discount = rounded(discount * cumulative / total)
            items.append(
                {
                    "description": row["description"],
                    "quantity": str(row["quantity"]),
                    "unit": row["unit"],
                    "unit_price": str(row["unit_price"]),
                    "gross_amount": str(amount),
                    "amount": str(amount - (next_discount - assigned)),
                }
            )
            assigned = next_discount
        return {
            "items": items,
            "gross_subtotal": str(total),
            "discount_amount": str(discount),
            "subtotal": str(net),
            "tax": str(tax),
            "withholding": str(withholding),
            "total": str(net + tax),
            "paid": str(paid),
        }


def lock_bill(pk):
    source = PurchaseBill.objects.select_related("supplier_purchase").get(pk=pk)
    if source.supplier_purchase_id:
        from procurement.models import ProcurementRequest

        ProcurementRequest.objects.select_for_update().get(
            pk=source.supplier_purchase.request_id
        )
    return (
        PurchaseBill.objects.select_for_update(of=("self",))
        .select_related("transaction", "supplier_purchase")
        .get(pk=pk)
    )


def posting_delivery(actor, bill, date, key):
    access(actor, "purchasebill", "deliver")
    require(actor, "ledger.add_purchasetransaction")
    if bill.transaction_id or date < bill.date or date > today():
        raise ValidationError(
            "Bill is already delivered, or delivery date is outside the bill date to today range."
        )
    record = post_party_transaction(
        actor=actor,
        payload={
            "request_key": str(key),
            "party_id": str(bill.party_id),
            "ledger_kind": "purchase",
            "transaction_type": "BILL",
            "currency": bill.currency,
            "date": str(date),
            "amount": str(bill.subtotal),
            "description": f"Delivered supplier invoice {bill.reference}",
            "reference": bill.reference,
        },
    )
    # A linked advance settles the bill as soon as delivery creates its debt.
    # Party lock from posting serializes this with payments/refunds/reallocations.
    from .models import PartyPaymentAllocation

    outstanding = record.amount
    for payment in (
        PurchasePayment.objects.filter(
            bill_id=bill.pk, currency=bill.currency, transaction__reversal__isnull=True
        )
        .select_related("transaction")
        .order_by("date", "created_at", "id")
    ):
        value = min(outstanding, remaining(payment.transaction))
        if value > 0:
            from django.db.models import Max

            dates = PartyPaymentAllocation.objects.filter(
                payment=payment.transaction
            ).aggregate(allocated=Max("effective_date"), released=Max("release__date"))
            corrected = payment.transaction.adjustments.aggregate(last=Max("date"))[
                "last"
            ]
            effective_date = max(
                v for v in [date, payment.date, corrected, *dates.values()] if v
            )
            PartyPaymentAllocation.objects.create(
                payment=payment.transaction,
                document=record,
                amount=value,
                effective_date=effective_date,
            )
            outstanding -= value
        if outstanding <= 0:
            break
    bill.transaction = record
    bill.delivered_at = date
    from .tax_posting import delivery_tax

    delivery_tax(bill, "purchase")
    if bill.supplier_purchase_id:
        ProcurementLedgerLink.objects.create(
            transaction=record,
            supplier_purchase=bill.supplier_purchase,
            request=bill.supplier_purchase.request,
        )


@transaction.atomic
def save_bill(*, actor, party_id, payload, bill_id=None, source=None):
    access(actor, "purchasebill", "change" if bill_id else "add")
    form = BillInput(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    canonical = {
        "party": str(party_id),
        "source": str(source.pk) if source else None,
        **form.data,
    }
    lock_submission(d["request_key"])
    previous = PurchaseBillEvent.objects.filter(request_key=d["request_key"]).first()
    action = "EDIT" if bill_id else "CREATE"
    if previous:
        if (
            previous.actor_id != actor.pk
            or previous.action != action
            or previous.payload != canonical
            or (bill_id and previous.bill_id != bill_id)
        ):
            raise ValidationError(
                "Submission identifier was used with different bill details."
            )
        return previous.bill
    bill = lock_bill(bill_id) if bill_id else None
    if bill and (
        bill.party_id != party_id
        or bill.version != d.get("version")
        or bill.transaction_id
    ):
        raise ValidationError(
            "Bill changed, belongs to another supplier or has already been delivered."
        )
    if bill and bill.supplier_purchase_id:
        raise ValidationError("Procurement-linked bills retain their source details.")
    party = Party.objects.select_for_update().filter(pk=party_id).first()
    if (
        not party
        or not Party.objects.active().with_role("SUPPLIER").filter(pk=party_id).exists()
    ):
        raise ValidationError("Choose an active supplier.")
    duplicates = PurchaseBill.objects.filter(
        party=party, reference__iexact=d["reference"]
    )
    if bill:
        duplicates = duplicates.exclude(pk=bill.pk)
    if (
        duplicates.exists()
        or PartyLedgerTransaction.objects.filter(
            party=party,
            ledger_kind="purchase",
            transaction_type="BILL",
            reference__iexact=d["reference"],
        ).exists()
    ):
        raise ValidationError("This supplier invoice reference already exists.")
    pricing = price(d)
    if bill:
        from .tax_posting import validate_document_edit

        validate_document_edit(
            bill,
            "purchase",
            Decimal(pricing["subtotal"]),
            Decimal(pricing["tax"]),
            d["currency"],
        )
    snapshot = {**form.data, **pricing}
    snapshot.pop("request_key", None)
    if not bill:
        bill = PurchaseBill(party=party, actor=actor, supplier_purchase=source)
    else:
        bill.version += 1
    bill.reference = d["reference"]
    bill.date = d["date"]
    bill.currency = d["currency"]
    bill.subtotal = Decimal(pricing["subtotal"])
    bill.snapshot = snapshot
    if d["delivered"]:
        posting_delivery(
            actor, bill, d.get("delivery_date", d["date"]), d["request_key"]
        )
    bill.save()
    PurchaseBillEvent.objects.create(
        request_key=d["request_key"],
        bill=bill,
        action=action,
        payload=canonical,
        snapshot=bill.snapshot,
        actor=actor,
    )
    return bill


@transaction.atomic
def deliver_bill(*, actor, pk, payload):
    access(actor, "purchasebill", "deliver")

    class Input(serializers.Serializer):
        request_key = serializers.UUIDField()
        version = serializers.IntegerField(min_value=1)
        delivery_date = serializers.DateField()

    form = Input(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    lock_submission(d["request_key"])
    previous = PurchaseBillEvent.objects.filter(request_key=d["request_key"]).first()
    if previous:
        if (
            previous.bill_id != pk
            or previous.action != "DELIVER"
            or previous.actor_id != actor.pk
            or previous.payload != dict(form.data)
        ):
            raise ValidationError(
                "Submission identifier was used with different delivery details."
            )
        return previous.bill
    bill = lock_bill(pk)
    if bill.version != d["version"]:
        raise ValidationError("Bill changed. Refresh before confirming delivery.")
    posting_delivery(actor, bill, d["delivery_date"], d["request_key"])
    bill.version += 1
    bill.snapshot = {
        **bill.snapshot,
        "delivered": True,
        "delivery_date": str(d["delivery_date"]),
    }
    bill.save()
    PurchaseBillEvent.objects.create(
        request_key=d["request_key"],
        bill=bill,
        action="DELIVER",
        payload=dict(form.data),
        snapshot=bill.snapshot,
        actor=actor,
    )
    return bill


def create_payment(*, actor, party_id, payload, uploaded_file=None):
    access(actor, "purchasepayment", "add")
    form = PaymentInput(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    from .purchase_uploads import validated_file, persist_evidence, cleanup

    file_info = validated_file(uploaded_file)
    canonical = {"party": str(party_id), **form.data, "file": file_info}
    uploaded = []
    try:
        with transaction.atomic():
            lock_submission(d["request_key"])
            previous = PurchasePayment.objects.filter(
                request_key=d["request_key"]
            ).first()
            if previous:
                if previous.actor_id != actor.pk or previous.submission != canonical:
                    raise ValidationError(
                        "Submission identifier was used with different payment details."
                    )
                return previous
            if (
                PartyLedgerTransaction.objects.filter(
                    request_key=d["request_key"]
                ).exists()
                or LedgerOperation.objects.filter(request_key=d["request_key"]).exists()
            ):
                raise ValidationError(
                    "Submission identifier belongs to another posting."
                )
            bill = None
            if d.get("bill_id"):
                access(actor, "purchasebill", "view")
                if not PurchaseBill.objects.filter(
                    pk=d["bill_id"], party_id=party_id
                ).exists():
                    raise ValidationError("Choose this supplier's invoice.")
                bill = lock_bill(d["bill_id"])
                if (
                    bill.currency != d["currency"]
                    or bill.version != d.get("bill_version")
                    or (bill.transaction_id and hasattr(bill.transaction, "reversal"))
                ):
                    raise ValidationError(
                        "Supplier invoice currency/version changed or it was reversed. Reload its draft."
                    )
            party = Party.objects.select_for_update().filter(pk=party_id).first()
            if (
                not party
                or not Party.objects.active()
                .with_role("SUPPLIER")
                .filter(pk=party_id)
                .exists()
            ):
                raise ValidationError("Choose an active supplier.")
            account = LedgerAccount.objects.filter(
                pk=d["account_id"], is_active=True, currency=d["currency"]
            ).first()
            if not account or (account.kind == "CASH") != (
                d["payment_method"] == "CASH"
            ):
                raise ValidationError(
                    "Choose a matching active cash/bank account in this currency."
                )
            if account.kind == "BANK" and not d["bank_name"]:
                raise ValidationError("Enter the paying bank name.")
            pricing = price(d)
            subtotal = Decimal(pricing["subtotal"])
            from .tax_posting import validate_payment, payment_tax

            validate_payment(
                bill, "purchase", subtotal, Decimal(pricing["tax"]), d["date"]
            )
            allocations = []
            if bill and bill.transaction_id and bill.transaction.date <= d["date"]:
                value = min(subtotal, remaining(bill.transaction))
                if value > 0:
                    allocations = [
                        {"document_id": str(bill.transaction_id), "amount": str(value)}
                    ]
            identity = uuid4()
            record = post_party_transaction(
                actor=actor,
                receipt_breakdown=(
                    Decimal(pricing["tax"]),
                    Decimal(pricing["withholding"]),
                ),
                payload={
                    "request_key": str(d["request_key"]),
                    "party_id": str(party_id),
                    "ledger_kind": "purchase",
                    "transaction_type": "PAYMENT",
                    "currency": d["currency"],
                    "date": str(d["date"]),
                    "amount": str(subtotal),
                    "account_id": str(account.pk),
                    "description": d["description"] or "Supplier payment",
                    "reference": f"PAY-{identity}",
                    "allocations": allocations,
                },
            )
            snapshot = {
                **form.data,
                **pricing,
                "supplier_name": party.display_name,
                "account_name": account.name,
                "bank_name": d["bank_name"] if account.kind == "BANK" else "",
                "bill_reference": bill.reference if bill else "",
                "allocations": allocations,
            }
            snapshot.pop("request_key", None)
            payment = PurchasePayment.objects.create(
                id=identity,
                request_key=d["request_key"],
                party=party,
                bill=bill,
                transaction=record,
                date=d["date"],
                currency=d["currency"],
                subtotal=subtotal,
                vat=Decimal(pricing["tax"]),
                withholding=Decimal(pricing["withholding"]),
                paid=Decimal(pricing["paid"]),
                snapshot=snapshot,
                submission=canonical,
                actor=actor,
            )
            payment_tax(payment, "purchase")
            persist_evidence(
                payment=payment,
                actor=actor,
                key=d["request_key"],
                number=d["receipt_number"],
                version=1,
                payload={"receipt_number": d["receipt_number"], "file": file_info},
                file=uploaded_file,
                info=file_info,
                uploaded=uploaded,
            )
            return payment
    except Exception:
        cleanup(uploaded)
        raise
