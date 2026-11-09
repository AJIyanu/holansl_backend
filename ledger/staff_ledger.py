"""Staff cash movements and approved itemized advance retirement, all append-only."""

from contextlib import contextmanager
from decimal import Decimal
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q, Sum, Max
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from crm.storage import get_document_storage, DocumentStorageError
from .models import (
    StaffLedgerEntry as Entry,
    StaffRetirement,
    StaffRetirementDecision,
    StaffLedgerDocument,
    StaffStatement,
    CashBookEntry,
    CashBookJournal,
    LedgerAccount,
)
from .operations import require
from .invoices import today, MAX_AMOUNT
from .idempotency import lock_submission
from .purchase_uploads import validated_file, cleanup

ZERO = Decimal("0.00")
PAY_CATEGORIES = [
    "SALARY",
    "ALLOWANCE",
    "TRANSPORT",
    "EXPENSE",
    "LOAN",
    "ADVANCE",
    "ADVANCE_REIMBURSEMENT",
    "OTHER_PAYMENT",
]
RECEIVE_CATEGORIES = [
    "ADVANCE_RETURN",
    "LOAN_REPAYMENT",
    "LEVY",
    "CONTRIBUTION",
    "OTHER_RECEIPT",
]
EXPENSE_CATEGORIES = [
    "TRANSPORT",
    "ACCOMMODATION",
    "MEALS",
    "PURCHASES",
    "COMMUNICATION",
    "OTHER",
]


def access(actor, action="view"):
    require(actor, "ledger.view_staffledger", f"ledger.{action}_staffledger")


def eligible_staff():
    return (
        get_user_model()
        .objects.filter(
            Q(is_staff=True)
            | Q(profile__isnull=False)
            | Q(pk__in=Entry.objects.values("staff_id"))
        )
        .distinct()
        .order_by("username", "id")
    )


def staff_name(staff):
    return staff.get_full_name().strip() or staff.username


def lock_staff(pk):
    # Lock only the User row (directory joins are unsuitable for FOR UPDATE).
    if not eligible_staff().filter(pk=pk).exists():
        raise ValidationError("Select an existing staff account.")
    return get_user_model().objects.select_for_update().get(pk=pk)


def ensure_key_unused(key):
    if any(
        model.objects.filter(request_key=key).exists()
        for model in [Entry, StaffRetirement, StaffRetirementDecision, StaffStatement]
    ):
        raise ValidationError(
            "Submission identifier belongs to another staff-ledger operation."
        )


def balance(source, as_of=None):
    rows = source.settlements.all()
    if as_of:
        rows = rows.filter(date__lte=as_of)
    totals = rows.aggregate(
        receivable=Sum("receivable_delta"), payable=Sum("payable_delta")
    )
    return source.receivable_delta + (
        totals["receivable"] or ZERO
    ), source.payable_delta + (totals["payable"] or ZERO)


def check_date(source, date):
    latest = source.settlements.aggregate(last=Max("date"))["last"]
    if date < source.date or (latest and date < latest):
        raise ValidationError(
            "Date cannot precede the linked advance/loan or its existing settlements."
        )


def file_details(files, allowed):
    if set(files) - set(allowed):
        raise ValidationError("A supporting document does not match a form item.")
    if any(f.size <= 0 for f in files.values()):
        raise ValidationError("Supporting documents must not be empty.")
    if sum(f.size for f in files.values()) > 20 * 1024 * 1024:
        raise ValidationError(
            "Supporting documents must total no more than 20 MB per submission."
        )
    return {key: validated_file(file) for key, file in files.items()}


@contextmanager
def evidence_transaction():
    uploaded = []
    try:
        with transaction.atomic():
            yield uploaded
    except Exception:
        cleanup(uploaded)
        raise


def save_documents(
    *, actor, staff, files, details, uploaded, entry=None, retirement=None
):
    for key, file in files.items():
        identity = uuid4()
        info = details[key]
        try:
            storage = get_document_storage()
            stored = storage.upload(
                document_id=identity,
                party_id=staff.pk,
                category="staff_ledger",
                filename=info["filename"],
                mime_type=info["mime_type"],
                size_bytes=info["size_bytes"],
                file_object=file,
            )
        except DocumentStorageError:
            raise ValidationError(
                "Unable to store the supporting document. No changes were saved."
            ) from None
        uploaded.append((storage, stored))
        StaffLedgerDocument.objects.create(
            id=identity,
            entry=entry,
            retirement=retirement,
            item_position=int(key.split("_")[1]) + 1 if retirement else None,
            actor=actor,
            **info,
            storage_provider=stored.provider,
            external_file_id=stored.external_file_id,
            storage_path=stored.storage_path,
        )


class MoneyInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=["PAY", "RECEIVE"])
    category = serializers.ChoiceField(choices=PAY_CATEGORIES + RECEIVE_CATEGORIES)
    date = serializers.DateField()
    currency = serializers.ChoiceField(choices=["NGN", "USD", "GBP"])
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal(".01")
    )
    account_id = serializers.UUIDField()
    description = serializers.CharField(max_length=1000)
    reference = serializers.CharField(max_length=100, allow_blank=True, default="")
    related_entry = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, d):
        if d["date"] > today():
            raise ValidationError("Record only money already paid or received.")
        if d["category"] not in (
            PAY_CATEGORIES if d["kind"] == "PAY" else RECEIVE_CATEGORIES
        ):
            raise ValidationError("Category does not match the direction of money.")
        linked = d["category"] in [
            "ADVANCE_RETURN",
            "LOAN_REPAYMENT",
            "ADVANCE_REIMBURSEMENT",
        ]
        if linked != bool(d.get("related_entry")):
            raise ValidationError(
                "Select an advance/loan only for its return, repayment or reimbursement."
            )
        return d


def post_money(*, actor, staff_id, payload, files=None):
    form = MoneyInput(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    access(actor, "pay" if d["kind"] == "PAY" else "receive")
    require(actor, "ledger.add_cashbookentry")
    files = files or {}
    details = file_details(files, ["file"])
    canonical = {"staff_id": str(staff_id), **form.data, "files": details}
    with evidence_transaction() as uploaded:
        lock_submission(d["request_key"])
        prior = Entry.objects.filter(request_key=d["request_key"]).first()
        if prior:
            if prior.actor_id != actor.pk or prior.payload != canonical:
                raise ValidationError(
                    "Submission identifier was used with different staff-payment details."
                )
            return prior
        ensure_key_unused(d["request_key"])
        staff = lock_staff(staff_id)
        source = None
        receivable, payable = ZERO, ZERO
        category = d["category"]
        amount = d["amount"]
        if d.get("related_entry"):
            source = Entry.objects.filter(
                pk=d["related_entry"],
                staff=staff,
                currency=d["currency"],
                category="LOAN" if category == "LOAN_REPAYMENT" else "ADVANCE",
                kind="PAY",
            ).first()
            if not source:
                raise ValidationError(
                    "Select this staff member's advance/loan in the same currency."
                )
            check_date(source, d["date"])
            outstanding, due = balance(source)
            if category == "ADVANCE_REIMBURSEMENT":
                if amount > due:
                    raise ValidationError("Payment exceeds approved reimbursement due.")
                payable = -amount
                debit, credit = "STAFF_PAYABLE", "CASH"
            else:
                if amount > outstanding:
                    raise ValidationError(
                        "Return or repayment exceeds the outstanding amount."
                    )
                receivable = -amount
                debit, credit = "CASH", "STAFF_RECEIVABLE"
        elif category in ["ADVANCE", "LOAN"]:
            receivable = amount
            debit, credit = "STAFF_RECEIVABLE", "CASH"
        elif d["kind"] == "PAY":
            debit, credit = "EXPENSE", "CASH"
        else:
            debit, credit = "CASH", "INCOME"
        account = LedgerAccount.objects.filter(
            pk=d["account_id"], currency=d["currency"], is_active=True
        ).first()
        if not account:
            raise ValidationError(
                "Choose an active paying/receiving account in this currency."
            )
        cash = CashBookEntry.objects.create(
            account=account,
            currency=d["currency"],
            date=d["date"],
            kind="EXPENSE" if d["kind"] == "PAY" else "RECEIPT",
            amount=amount,
            description=d["description"][:500],
            reference=d["reference"],
            created_by=actor,
            updated_by=actor,
        )
        entry = Entry.objects.create(
            request_key=d["request_key"],
            staff=staff,
            kind=d["kind"],
            category=category,
            date=d["date"],
            currency=d["currency"],
            amount=amount,
            description=d["description"],
            reference=d["reference"],
            related_entry=source,
            cash_entry=cash,
            receivable_delta=receivable,
            payable_delta=payable,
            payload=canonical,
            actor=actor,
        )
        CashBookJournal.objects.create(
            staff_entry=entry,
            entry=cash,
            currency=entry.currency,
            date=entry.date,
            amount=amount,
            debit_account=debit,
            credit_account=credit,
            description=entry.description[:500],
            reference=entry.reference,
            actor=actor,
        )
        save_documents(
            actor=actor,
            staff=staff,
            files=files,
            details=details,
            uploaded=uploaded,
            entry=entry,
        )
        return entry


class ExpenseInput(serializers.Serializer):
    date = serializers.DateField()
    category = serializers.ChoiceField(choices=EXPENSE_CATEGORIES)
    description = serializers.CharField(max_length=1000)
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal(".01")
    )


class RetirementInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    advance_id = serializers.UUIDField()
    date = serializers.DateField()
    description = serializers.CharField(max_length=1000)
    items = ExpenseInput(many=True, min_length=1, max_length=100)

    def validate(self, d):
        total = sum((r["amount"] for r in d["items"]), ZERO)
        if total > MAX_AMOUNT:
            raise ValidationError("Total expenses exceed the supported amount.")
        if d["date"] > today() or any(r["date"] > d["date"] for r in d["items"]):
            raise ValidationError(
                "Expense dates must be on/before the retirement date, and not in the future."
            )
        return d


def submit_retirement(*, actor, staff_id, payload, files=None):
    access(actor, "retire")
    form = RetirementInput(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    files = files or {}
    details = file_details(files, [f"item_{i}" for i in range(len(d["items"]))])
    canonical = {"staff_id": str(staff_id), **form.data, "files": details}
    with evidence_transaction() as uploaded:
        lock_submission(d["request_key"])
        prior = StaffRetirement.objects.filter(request_key=d["request_key"]).first()
        if prior:
            if prior.actor_id != actor.pk or prior.payload != canonical:
                raise ValidationError(
                    "Submission identifier was used with different expense details."
                )
            return prior
        ensure_key_unused(d["request_key"])
        staff = lock_staff(staff_id)
        advance = Entry.objects.filter(
            pk=d["advance_id"], staff=staff, kind="PAY", category="ADVANCE"
        ).first()
        if not advance:
            raise ValidationError("Choose this staff member's advance.")
        if any(r["date"] < advance.date for r in d["items"]):
            raise ValidationError("Expense dates must not precede the advance.")
        if (
            StaffRetirement.objects.filter(advance=advance)
            .filter(Q(decision__isnull=True) | Q(decision__approved=True))
            .exists()
        ):
            raise ValidationError(
                "This advance already has a pending or approved retirement."
            )
        row = StaffRetirement.objects.create(
            request_key=d["request_key"],
            advance=advance,
            date=d["date"],
            description=d["description"],
            total=sum((r["amount"] for r in d["items"]), ZERO),
            items=[
                {"position": i + 1, **item} for i, item in enumerate(form.data["items"])
            ],
            payload=canonical,
            actor=actor,
        )
        save_documents(
            actor=actor,
            staff=staff,
            files=files,
            details=details,
            uploaded=uploaded,
            retirement=row,
        )
        return row


@transaction.atomic
def decide_retirement(*, actor, pk, payload):
    access(actor, "approve")

    class Input(serializers.Serializer):
        request_key = serializers.UUIDField()
        approved = serializers.BooleanField()
        date = serializers.DateField()
        reason = serializers.CharField(max_length=1000)
        expected_remaining = serializers.DecimalField(
            max_digits=18, decimal_places=2, min_value=0
        )

    form = Input(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    lock_submission(d["request_key"])
    prior = StaffRetirementDecision.objects.filter(request_key=d["request_key"]).first()
    if prior:
        if (
            prior.actor_id != actor.pk
            or str(prior.retirement_id) != str(pk)
            or prior.payload != dict(form.data)
        ):
            raise ValidationError(
                "Submission identifier was used with different approval details."
            )
        return prior
    ensure_key_unused(d["request_key"])
    row = StaffRetirement.objects.select_related("advance").get(pk=pk)
    staff = lock_staff(row.advance.staff_id)
    if StaffRetirementDecision.objects.filter(retirement=row).exists():
        raise ValidationError("This retirement has already been decided.")
    if d["date"] < row.date or d["date"] > today():
        raise ValidationError(
            "Decision date must be between submission date and today."
        )
    remaining, _ = balance(row.advance)
    if remaining != d["expected_remaining"]:
        raise ValidationError("The advance balance changed. Refresh before approving.")
    entry = None
    if d["approved"]:
        check_date(row.advance, d["date"])
        if StaffRetirementDecision.objects.filter(
            advance=row.advance, approved=True
        ).exists():
            raise ValidationError("This advance is already retired.")
        used = min(row.total, remaining)
        owed = max(ZERO, row.total - remaining)
        entry = Entry.objects.create(
            request_key=d["request_key"],
            staff=staff,
            kind="RETIRE",
            category="EXPENSE_BREAKDOWN",
            date=d["date"],
            currency=row.advance.currency,
            amount=row.total,
            description=row.description,
            related_entry=row.advance,
            receivable_delta=-used,
            payable_delta=owed,
            payload={"retirement_id": str(row.pk), **form.data},
            actor=actor,
        )
        available = remaining
        for item in row.items:
            amount = Decimal(item["amount"])
            from_advance = min(available, amount)
            for value, credit in [
                (from_advance, "STAFF_RECEIVABLE"),
                (amount - from_advance, "STAFF_PAYABLE"),
            ]:
                if value > 0:
                    CashBookJournal.objects.create(
                        staff_entry=entry,
                        currency=entry.currency,
                        date=entry.date,
                        amount=value,
                        debit_account="EXPENSE",
                        credit_account=credit,
                        description=f"{item['position']}. {item['category']}: {item['description']}"[
                            :500
                        ],
                        reference=row.advance.reference,
                        actor=actor,
                    )
            available -= from_advance
    return StaffRetirementDecision.objects.create(
        request_key=d["request_key"],
        retirement=row,
        advance=row.advance,
        approved=d["approved"],
        date=d["date"],
        reason=d["reason"],
        entry=entry,
        payload=dict(form.data),
        actor=actor,
    )
