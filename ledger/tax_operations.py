"""Permissioned preparation and explicit approval of tax settlements and adjustments."""

from copy import copy
from decimal import Decimal
from uuid import uuid4
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from .models import (
    TaxEntry,
    TaxRequest,
    TaxDecision,
    TaxDocument,
    LedgerAccount,
    CashBookEntry,
    SalesInvoice,
    PurchaseBill,
)
from .operations import require
from .invoices import today
from .idempotency import lock_submission
from .tax_posting import (
    totals,
    lock_currency,
    journal,
    ZERO,
    CLASS_FIELD,
    CLASS_ACCOUNT,
    category,
    ensure_assets,
    post_event,
    require_history,
)
from .purchase_uploads import validated_file, cleanup
from crm.storage import get_document_storage, DocumentStorageError

ACTIONS = {
    "REMIT": "remit",
    "CIT_ASSESS": "adjust",
    "CIT_CREDIT": "apply",
    "CLASSIFY": "configure",
    "ADJUST": "adjust",
    "VAT_SETTLE": "remit",
    "OPENING": "adjust",
    "REVERSE": "adjust",
}
ADJUST_ACCOUNTS = {
    "output_vat": ("ADJUSTMENT", "VAT_PAYABLE"),
    "input_vat": ("INPUT_TAX", "ADJUSTMENT"),
    "input_pending": ("INPUT_PENDING", "ADJUSTMENT"),
    "input_nonrecoverable": ("EXPENSE", "ADJUSTMENT"),
    "wht_asset": ("WHT_RECEIVABLE", "ADJUSTMENT"),
    "wht_liability": ("ADJUSTMENT", "WHT_PAYABLE"),
    "cit_assessed": ("TAX_EXPENSE", "CIT_PAYABLE"),
    "wht_used": ("CIT_PAYABLE", "WHT_RECEIVABLE"),
    "input_used": ("VAT_PAYABLE", "INPUT_TAX"),
}


def access(actor, action="view"):
    require(actor, "ledger.view_taxledger", f"ledger.{action}_taxledger")


class RequestInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    source_entry = serializers.UUIDField(required=False)
    action = serializers.ChoiceField(choices=list(ACTIONS))
    date = serializers.DateField()
    period = serializers.RegexField(r"^\d{4}-(0[1-9]|1[0-2])$")
    currency = serializers.ChoiceField(choices=["NGN", "USD", "GBP"])
    tax_type = serializers.ChoiceField(choices=["VAT", "WHT", "CIT"], default="VAT")
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal(".01"), required=False
    )
    authority = serializers.CharField(max_length=255, allow_blank=True, default="")
    reference = serializers.CharField(max_length=100, allow_blank=True, default="")
    description = serializers.CharField(max_length=1000)
    account_id = serializers.UUIDField(required=False)
    document_key = serializers.CharField(max_length=100, allow_blank=True, default="")
    classification = serializers.ChoiceField(
        choices=list(CLASS_FIELD), default="AVAILABLE"
    )
    adjust_field = serializers.ChoiceField(
        choices=list(ADJUST_ACCOUNTS), required=False
    )
    decrease = serializers.BooleanField(default=False)

    def validate(self, d):
        if d["date"] > today():
            raise ValidationError("Record only completed tax activity.")
        if d["action"] not in ["CLASSIFY", "OPENING", "REVERSE"] and not d.get(
            "amount"
        ):
            raise ValidationError("Enter a positive amount.")
        if d["action"] in ["REMIT", "VAT_SETTLE"] and not d.get("account_id"):
            raise ValidationError("Select the paying or receiving account.")
        if d["action"] == "REMIT" and (not d["authority"] or not d["reference"]):
            raise ValidationError(
                "Remittances require the authority and payment reference."
            )
        if (
            d["action"] in ["CLASSIFY", "VAT_SETTLE", "OPENING"]
            and not d["document_key"]
        ):
            raise ValidationError("Select a source document.")
        if d["action"] == "REVERSE" and not d.get("source_entry"):
            raise ValidationError("Select the tax posting to reverse.")
        if d["action"] == "ADJUST" and not d.get("adjust_field"):
            raise ValidationError("Select the balance to adjust.")
        if d["period"] > d["date"].strftime("%Y-%m"):
            raise ValidationError("Tax period cannot follow the accounting date.")
        return d


def prepare(*, actor, payload, file=None):
    form = RequestInput(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    access(actor, ACTIONS[d["action"]])
    if file is not None and file.size == 0:
        raise ValidationError("Supporting document is empty.")
    info = validated_file(file)
    canonical = {**form.data, "file": info}
    uploaded = []
    try:
        with transaction.atomic():
            lock_submission(d["request_key"])
            prior = TaxRequest.objects.filter(request_key=d["request_key"]).first()
            if prior:
                if prior.actor_id != actor.pk or prior.payload != canonical:
                    raise ValidationError(
                        "Submission identifier was used with different tax details."
                    )
                return prior
            if TaxDecision.objects.filter(request_key=d["request_key"]).exists():
                raise ValidationError("Submission identifier already used.")
            row = TaxRequest.objects.create(
                request_key=d["request_key"], payload=canonical, actor=actor
            )
            if info:
                try:
                    storage = get_document_storage()
                    identity = uuid4()
                    stored = storage.upload(
                        document_id=identity,
                        party_id=actor.pk,
                        category="tax_ledger",
                        filename=info["filename"],
                        mime_type=info["mime_type"],
                        size_bytes=info["size_bytes"],
                        file_object=file,
                    )
                except DocumentStorageError:
                    raise ValidationError(
                        "Supporting document upload failed. Nothing was saved."
                    ) from None
                uploaded.append((storage, stored))
                TaxDocument.objects.create(
                    id=identity,
                    request=row,
                    **info,
                    storage_provider=stored.provider,
                    external_file_id=stored.external_file_id,
                    storage_path=stored.storage_path,
                )
            return row
    except Exception:
        cleanup(uploaded)
        raise


def document(key):
    try:
        side, identity = key.split(":")
        from uuid import UUID

        identity = UUID(identity)
    except (ValueError, TypeError):
        raise ValidationError("Choose an invoice or supplier bill.") from None
    if side == "sales":
        from .invoices import lock_invoice

        if not SalesInvoice.objects.filter(pk=identity).exists():
            raise ValidationError("Invoice not found.")
        doc = lock_invoice(identity)
        txn = doc.posted_transaction
    elif side == "purchase":
        from .purchases import lock_bill

        if not PurchaseBill.objects.filter(pk=identity).exists():
            raise ValidationError("Supplier bill not found.")
        doc = lock_bill(identity)
        txn = doc.transaction
    else:
        raise ValidationError("Choose an invoice or supplier bill.")
    from crm.models import Party

    Party.objects.select_for_update().get(pk=doc.party_id)
    return side, doc, txn


def balances(currency, date=None, period=None):
    rows = TaxEntry.objects.filter(currency=currency)
    if date:
        rows = rows.filter(date__lte=date)
    if period:
        rows = rows.filter(period__lte=period)
    t = totals(rows)
    return {
        **t,
        "vat_due": t["output_vat"] - t["input_vat"] - t["vat_remitted"],
        "input_available": t["input_vat"] - t["input_used"],
        "wht_available": t["wht_asset"] - t["wht_used"],
        "wht_due": t["wht_liability"] - t["wht_remitted"],
        "cit_due": t["cit_assessed"] - t["wht_used"] - t["cit_paid"],
    }


@transaction.atomic
def decide(*, actor, pk, payload):
    access(actor, "approve")

    class Input(serializers.Serializer):
        request_key = serializers.UUIDField()
        approved = serializers.BooleanField()
        reason = serializers.CharField(max_length=1000)

    form = Input(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    lock_submission(d["request_key"])
    prior = TaxDecision.objects.filter(request_key=d["request_key"]).first()
    if prior:
        if (
            str(prior.request_id) != str(pk)
            or prior.actor_id != actor.pk
            or prior.payload != dict(form.data)
        ):
            raise ValidationError(
                "Submission identifier was used for a different decision."
            )
        return prior
    if TaxRequest.objects.filter(request_key=d["request_key"]).exists():
        raise ValidationError("Submission identifier already used.")
    request = TaxRequest.objects.select_for_update().get(pk=pk)
    if hasattr(request, "decision"):
        raise ValidationError("This tax request has already been decided.")
    data = RequestInput(data=request.payload)
    data.is_valid(raise_exception=True)
    v = data.validated_data
    access(actor, ACTIONS[v["action"]])
    entry = execute(actor, request, v) if d["approved"] else None
    return TaxDecision.objects.create(
        request=request,
        request_key=d["request_key"],
        approved=d["approved"],
        reason=d["reason"],
        payload=dict(form.data),
        entry=entry,
        actor=actor,
    )


def execute(actor, request, d):
    action = d["action"]
    key = d["document_key"]
    side = doc = txn = None
    if action in ["OPENING", "VAT_SETTLE"]:
        side, doc, txn = document(key)
        require(actor, f"ledger.view_{side}ledger")
        if doc.currency != d["currency"] or not txn or hasattr(txn, "reversal"):
            raise ValidationError(
                "Select a delivered, unreversed document in this currency."
            )
    lock_currency(d["currency"])
    if action == "REVERSE":
        return reverse_operation(actor, request, d)
    t = balances(d["currency"], d["date"], d["period"])
    current = balances(d["currency"])
    if action == "CIT_CREDIT" or (action == "REMIT" and d["tax_type"] == "CIT"):
        annual = totals(
            TaxEntry.objects.filter(
                currency=d["currency"],
                period__startswith=d["period"][:4],
                date__lte=d["date"],
            )
        )
        annual_due = annual["cit_assessed"] - annual["wht_used"] - annual["cit_paid"]
        t["cit_due"] = min(t["cit_due"], annual_due)
    amount = d.get("amount", ZERO)
    if action in ["REMIT", "CIT_CREDIT"]:
        field = (
            "cit_due"
            if action == "CIT_CREDIT"
            else {"VAT": "vat_due", "WHT": "wht_due", "CIT": "cit_due"}[d["tax_type"]]
        )
        if amount > min(t[field], current[field]):
            raise ValidationError(
                "Amount exceeds the outstanding tax balance for this period/date or the current balance."
            )
    if action == "CIT_CREDIT" and amount > min(
        t["wht_available"], current["wht_available"]
    ):
        raise ValidationError("Insufficient unused client WHT credit.")
    if action == "OPENING":
        if TaxEntry.objects.filter(source_key=f"delivery:{txn.pk}").exists():
            raise ValidationError("Delivery VAT is already recorded.")
        require_history(doc, side)
        historical = copy(txn)
        historical.date = d["date"]
        historical.description = d["description"]
        from .tax_posting import remaining_delivery_vat

        vat = remaining_delivery_vat(doc, side)
        return post_event(
            source_key=f"delivery:{txn.pk}",
            key=key,
            side=side,
            txn=historical,
            delivery=vat,
            actor=actor,
            kind="OPENING",
            metadata={
                "request": str(request.pk),
                "original_delivery_date": str(txn.date),
            },
        )
    group = (
        totals(TaxEntry.objects.filter(document_key=key, currency=d["currency"]))
        if key
        else None
    )
    if action in ["VAT_SETTLE", "CLASSIFY"]:
        if TaxEntry.objects.filter(document_key=key, date__gt=d["date"]).exists():
            raise ValidationError(
                "Tax activity cannot precede the document’s latest tax event."
            )
    if action == "VAT_SETTLE":
        if (
            amount > group["party_vat"]
            or not TaxEntry.objects.filter(source_key=f"delivery:{txn.pk}").exists()
        ):
            raise ValidationError(
                "Amount exceeds the document’s recorded outstanding VAT."
            )
    cash = None
    if action in ["REMIT", "VAT_SETTLE"]:
        require(actor, "ledger.add_cashbookentry")
        account = LedgerAccount.objects.filter(
            pk=d["account_id"], currency=d["currency"], is_active=True
        ).first()
        if not account:
            raise ValidationError(
                "Select an active cash/bank account in this currency."
            )
        cash = CashBookEntry.objects.create(
            account=account,
            currency=d["currency"],
            date=d["date"],
            kind="RECEIPT" if action == "VAT_SETTLE" and side == "sales" else "EXPENSE",
            amount=amount,
            description=d["description"][:500],
            reference=d["reference"],
            created_by=actor,
            updated_by=actor,
        )
    values = {}
    pairs = []
    metadata = {
        **request.payload,
        "request_id": str(request.pk),
        "prepared_by": str(request.actor_id),
    }
    if action == "REMIT":
        field, account = {
            "VAT": ("vat_remitted", "VAT_PAYABLE"),
            "WHT": ("wht_remitted", "WHT_PAYABLE"),
            "CIT": ("cit_paid", "CIT_PAYABLE"),
        }[d["tax_type"]]
        values[field] = amount
        pairs.append((amount, account, "CASH"))
        if d["tax_type"] == "VAT":
            offset = min(
                max(ZERO, t["input_available"]),
                max(ZERO, current["input_available"]),
                max(
                    ZERO, t["output_vat"] - t["input_used"] - t["vat_remitted"] - amount
                ),
            )
            values["input_used"] = offset
            pairs.append((offset, "VAT_PAYABLE", "INPUT_TAX"))
    elif action == "CIT_ASSESS":
        values["cit_assessed"] = amount
        pairs.append((amount, "TAX_EXPENSE", "CIT_PAYABLE"))
    elif action == "CIT_CREDIT":
        values["wht_used"] = amount
        pairs.append((amount, "CIT_PAYABLE", "WHT_RECEIVABLE"))
    elif action == "CLASSIFY":
        if (
            not key.startswith("purchase:")
            or not TaxEntry.objects.filter(
                document_key=key, currency=d["currency"], side="purchase"
            ).exists()
        ):
            raise ValidationError("Select a purchase tax source.")
        old = category(key)
        new = d["classification"]
        if old == new:
            raise ValidationError("Input VAT already has this classification.")
        amount = (
            group["input_vat"] + group["input_pending"] + group["input_nonrecoverable"]
        )
        if amount <= 0:
            raise ValidationError("No input VAT remains to classify.")
        if old == "AVAILABLE" and amount > current["input_available"]:
            raise ValidationError(
                "Input credit has already been used; reverse its application first."
            )
        values[CLASS_FIELD[old]] = -amount
        values[CLASS_FIELD[new]] = amount
        pairs.append((amount, CLASS_ACCOUNT[new], CLASS_ACCOUNT[old]))
        metadata["classification"] = new
    elif action == "ADJUST":
        amount = -amount if d["decrease"] else amount
        values[d["adjust_field"]] = amount
        pairs.append((amount, *ADJUST_ACCOUNTS[d["adjust_field"]]))
    elif action == "VAT_SETTLE":
        values["sales_vat_cash" if side == "sales" else "purchase_vat_cash"] = amount
        values["party_vat"] = -amount
        pairs.append(
            (
                amount,
                "CASH" if side == "sales" else "PAYABLE",
                "RECEIVABLE" if side == "sales" else "CASH",
            )
        )
    entry = TaxEntry.objects.create(
        source_key=f"request:{request.pk}",
        document_key=key,
        kind=action,
        side=side or ("purchase" if action == "CLASSIFY" else ""),
        date=d["date"],
        period=d["period"],
        currency=d["currency"],
        party=doc.party
        if doc
        else (
            TaxEntry.objects.filter(document_key=key).first().party
            if action == "CLASSIFY"
            else None
        ),
        cash_entry=cash,
        reference=d["reference"],
        description=d["description"],
        actor=actor,
        metadata=metadata,
        **values,
    )
    for value, debit, credit in pairs:
        journal(
            entry,
            value,
            debit,
            credit,
            cash=cash if debit == "CASH" or credit == "CASH" else None,
        )
    ensure_assets(d["currency"])
    after = balances(d["currency"])
    if (
        min(
            after["input_available"],
            after["cit_due"],
            after["wht_available"],
            after["wht_due"],
        )
        < 0
    ):
        raise ValidationError(
            "This operation would overdraw a tax credit or over-settle a liability."
        )
    return entry


def reverse_operation(actor, request, d):
    from .models import CashBookJournal
    from .tax_posting import FIELDS

    source = (
        TaxEntry.objects.filter(pk=d["source_entry"], currency=d["currency"])
        .select_related("cash_entry")
        .first()
    )
    if not source or source.kind not in ["REMIT", "CIT_CREDIT", "CIT_ASSESS", "ADJUST"]:
        raise ValidationError(
            "Choose a remittance, CIT assessment, CIT credit or manual adjustment."
        )
    if source.date > d["date"] or source.period != d["period"]:
        raise ValidationError(
            "Use the original tax period and a date on or after the original posting."
        )
    if TaxEntry.objects.filter(source_key=f"reverse:{source.pk}").exists():
        raise ValidationError("This posting has already been reversed.")
    cash = None
    if source.cash_entry_id:
        require(actor, "ledger.add_cashbookentry")
        old = source.cash_entry
        cash = CashBookEntry.objects.create(
            account=old.account,
            currency=old.currency,
            date=d["date"],
            kind="RECEIPT" if old.kind == "EXPENSE" else "EXPENSE",
            amount=old.amount,
            description=d["description"][:500],
            reference=d["reference"],
            created_by=actor,
            updated_by=actor,
        )
    entry = TaxEntry.objects.create(
        source_key=f"reverse:{source.pk}",
        kind="REVERSE",
        date=d["date"],
        period=source.period,
        currency=source.currency,
        actor=actor,
        cash_entry=cash,
        reference=d["reference"],
        description=d["description"],
        metadata={
            "source_entry": str(source.pk),
            "request_id": str(request.pk),
            "prepared_by": str(request.actor_id),
        },
        **{f: -getattr(source, f) for f in FIELDS},
    )
    for original in CashBookJournal.objects.filter(reason=f"Tax ledger {source.pk}"):
        journal(
            entry,
            original.amount,
            original.credit_account,
            original.debit_account,
            cash=cash if original.entry_id else None,
        )
    ensure_assets(source.currency)
    year = totals(
        TaxEntry.objects.filter(
            currency=source.currency, period__startswith=source.period[:4]
        )
    )
    if (
        balances(source.currency)["cit_due"] < 0
        or year["cit_assessed"] - year["cit_paid"] - year["wht_used"] < 0
    ):
        raise ValidationError(
            "Reverse the CIT settlement before reversing its assessment."
        )
    return entry
