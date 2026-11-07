"""Shared party-ledger selection, financial history and posting endpoints."""

from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from crm.models import Party
from .models import PartyLedgerSelection, PartyLedgerTransaction


class PartyLedgerPermission(BasePermission):
    def has_permission(self, request, view):
        kind = view.kwargs["kind"]
        if kind not in PartyLedgerSelection.Kind.values:
            raise NotFound()
        user = request.user
        return bool(
            user.is_authenticated
            and user.is_active
            and (user.is_staff or user.is_superuser)
            and user.has_perm(f"ledger.view_{kind}ledger")
        )


def eligible_parties(kind):
    role = "CLIENT" if kind == "sales" else "SUPPLIER"
    from django.db.models import Q

    eligible = Party.objects.directory().with_role(role).values("pk")
    from .models import SalesInvoice

    invoices = SalesInvoice.objects.values("party_id") if kind == "sales" else []
    historical = PartyLedgerTransaction.objects.filter(ledger_kind=kind).values(
        "party_id"
    )
    return Party.objects.filter(
        Q(pk__in=eligible) | Q(pk__in=historical) | Q(pk__in=invoices)
    ).order_by("display_name", "id")


class LedgerPartySerializer(serializers.ModelSerializer):
    class Meta:
        model = Party
        fields = ["id", "display_name", "entity_kind", "status"]
        read_only_fields = fields


class SelectionInputSerializer(serializers.Serializer):
    party_id = serializers.UUIDField(allow_null=True)


class PartyLedgerSelectionView(GenericAPIView):
    permission_classes = [PartyLedgerPermission]
    serializer_class = LedgerPartySerializer

    def get(self, request, kind):
        selection = PartyLedgerSelection.objects.filter(
            user=request.user, kind=kind
        ).first()
        party = (
            eligible_parties(kind).filter(pk=selection.party_id).first()
            if selection and selection.party_id
            else None
        )
        return Response(
            {
                "kind": kind,
                "selected_party": self.get_serializer(party).data if party else None,
            }
        )

    def put(self, request, kind):
        serializer = SelectionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        party_id = serializer.validated_data["party_id"]
        party = (
            get_object_or_404(eligible_parties(kind), pk=party_id) if party_id else None
        )
        PartyLedgerSelection.objects.update_or_create(
            user=request.user, kind=kind, defaults={"party": party}
        )
        return Response(
            {
                "kind": kind,
                "selected_party": self.get_serializer(party).data if party else None,
            }
        )


class PartyLedgerLookupView(GenericAPIView):
    permission_classes = [PartyLedgerPermission]
    serializer_class = LedgerPartySerializer

    def get(self, request, kind):
        class QuerySerializer(serializers.Serializer):
            search = serializers.CharField(max_length=100, allow_blank=True, default="")

        query = QuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        parties = eligible_parties(kind)
        search = query.validated_data["search"]
        if search:
            parties = parties.filter(display_name__icontains=search)
        page = self.paginate_queryset(parties)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)


class PartyTransactionSerializer(serializers.ModelSerializer):
    remaining_amount = serializers.SerializerMethodField()

    def get_remaining_amount(self, obj):
        from .balances import remaining

        value = getattr(obj, "remaining_value", None)
        return f"{remaining(obj) if value is None else value:.2f}"

    created_by_name = serializers.CharField(
        source="created_by.username", read_only=True
    )
    running_balance = serializers.DecimalField(
        max_digits=24, decimal_places=2, read_only=True
    )
    debit_account = serializers.CharField(
        source="journal.debit_account", read_only=True
    )
    credit_account = serializers.CharField(
        source="journal.credit_account", read_only=True
    )

    class Meta:
        model = PartyLedgerTransaction
        exclude = ["request_key", "allocation_request"]


class PartyLedgerTransactionsView(GenericAPIView):
    permission_classes = [PartyLedgerPermission]
    serializer_class = PartyTransactionSerializer

    def post(self, request, kind, party_id):
        from .party_posting import PostingInput, post_party_transaction
        from rest_framework.exceptions import PermissionDenied

        if not request.user.has_perm(f"ledger.add_{kind}transaction"):
            raise PermissionDenied()
        if not isinstance(request.data, dict):
            raise serializers.ValidationError("Expected a transaction object.")
        payload = {**request.data, "party_id": str(party_id), "ledger_kind": kind}
        validated = PostingInput(data=payload)
        validated.is_valid(raise_exception=True)
        if (
            validated.validated_data["transaction_type"] != "PAYMENT"
            and not validated.validated_data["reference"]
        ):
            raise serializers.ValidationError(
                {"reference": "An invoice or bill reference is required."}
            )
        if (
            kind == "sales"
            and validated.validated_data["transaction_type"] == "INVOICE"
        ):
            raise serializers.ValidationError(
                "Create an invoice document and mark it delivered to post it to the sales ledger."
            )
        record = post_party_transaction(actor=request.user, payload=payload)
        return Response(self.get_serializer(record).data, status=201)

    def get(self, request, kind, party_id):
        from decimal import Decimal
        from django.db.models import Case, When, F, Q, Sum, Window, DecimalField
        from .views import period

        # Historical records remain readable if a CRM role/status changes.
        get_object_or_404(eligible_parties(kind), pk=party_id)
        year, start, end = period(request)
        currency = request.query_params.get("currency", "NGN")
        if currency not in ("NGN", "USD", "GBP"):
            raise serializers.ValidationError("Choose NGN, USD or GBP.")
        records = PartyLedgerTransaction.objects.filter(
            party_id=party_id, ledger_kind=kind, currency=currency
        )
        signed = Case(
            When(effect=-1, then=-F("amount")),
            default=F("amount"),
            output_field=DecimalField(max_digits=24, decimal_places=2),
        )
        opening = records.filter(date__lt=start).aggregate(total=Sum(signed))[
            "total"
        ] or Decimal("0")
        selected = records.filter(date__range=(start, end))
        totals = selected.aggregate(
            charges=Sum("amount", filter=Q(effect=1)),
            payments=Sum("amount", filter=Q(effect=-1)),
            actual_payments=Sum("amount", filter=Q(transaction_type="PAYMENT")),
            invoices=Sum("amount", filter=Q(transaction_type__in=["INVOICE", "BILL"])),
            refunds=Sum("amount", filter=Q(transaction_type="REFUND")),
        )
        charges, payments = (
            totals["charges"] or Decimal("0"),
            totals["payments"] or Decimal("0"),
        )
        selected = selected.select_related("created_by", "journal").annotate(
            running_balance=Window(
                Sum(signed),
                order_by=[F("date").asc(), F("created_at").asc(), F("id").asc()],
            )
            + opening
        )
        page = self.paginate_queryset(selected)
        from .balances import remaining_balances

        balances = remaining_balances(page)
        for row in page:
            row.remaining_value = balances.get(row.pk, Decimal("0"))
        response = self.get_paginated_response(
            self.get_serializer(page, many=True).data
        )
        response.data["summary"] = {
            "currency": currency,
            "year": year,
            "date_from": str(start),
            "date_to": str(end),
            "opening_balance": f"{opening:.2f}",
            "charges": f"{charges:.2f}",
            "credits": f"{payments:.2f}",
            "payments": f"{totals['actual_payments'] or Decimal('0'):.2f}",
            "invoices": f"{totals['invoices'] or Decimal('0'):.2f}",
            "refunds": f"{totals['refunds'] or Decimal('0'):.2f}",
            "closing_balance": f"{opening + charges - payments:.2f}",
        }
        return response


class PartyOutstandingDocumentsView(GenericAPIView):
    permission_classes = [PartyLedgerPermission]
    serializer_class = PartyTransactionSerializer

    def get(self, request, kind, party_id):
        from .balances import remaining_balances

        get_object_or_404(eligible_parties(kind), pk=party_id)
        currency = serializers.ChoiceField(
            choices=["NGN", "USD", "GBP"]
        ).run_validation(request.query_params.get("currency", "NGN"))
        query = serializers.CharField(max_length=100, allow_blank=True).run_validation(
            request.query_params.get("search", "")
        )
        records = PartyLedgerTransaction.objects.filter(
            party_id=party_id,
            ledger_kind=kind,
            currency=currency,
            transaction_type="INVOICE" if kind == "sales" else "BILL",
            reversal__isnull=True,
        )
        if query:
            from django.db.models import Q

            records = records.filter(
                Q(reference__icontains=query) | Q(description__icontains=query)
            )
        rows = []
        records = list(records.order_by("date", "created_at", "id"))
        balances = remaining_balances(records)
        for row in records:
            balance = balances[row.pk]
            if balance > 0:
                rows.append(
                    {
                        "id": str(row.pk),
                        "date": str(row.date),
                        "reference": row.reference,
                        "description": row.description,
                        "amount": f"{row.amount:.2f}",
                        "outstanding": f"{balance:.2f}",
                    }
                )
        return self.get_paginated_response(self.paginate_queryset(rows))
