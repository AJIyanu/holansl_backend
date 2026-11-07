from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import LedgerAccount, Reconciliation, ReconciliationReview
from .party_ledgers import PartyLedgerPermission, eligible_parties
from .operations import operate, require
from .reconciliation import snapshot, review


class OperationsView(GenericAPIView):
    permission_classes = [PartyLedgerPermission]

    def post(self, request, kind, party_id):
        get_object_or_404(eligible_parties(kind), pk=party_id)
        return Response(
            operate(
                actor=request.user, party_id=party_id, kind=kind, payload=request.data
            ),
            status=201,
        )


class StatementSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(
        source="created_by.username", read_only=True
    )

    class Meta:
        model = Reconciliation
        exclude = ["request_key"]


class ReviewSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True)

    class Meta:
        model = ReconciliationReview
        exclude = ["request_key"]


class StatementsView(GenericAPIView):
    permission_classes = [PartyLedgerPermission]

    def get(self, request, kind, party_id):
        require(request.user, "ledger.view_reconciliation")
        get_object_or_404(eligible_parties(kind), pk=party_id)
        rows = Reconciliation.objects.filter(
            party_id=party_id, ledger_kind=kind
        ).select_related("created_by")
        return self.get_paginated_response(
            StatementSerializer(self.paginate_queryset(rows), many=True).data
        )

    def post(self, request, kind, party_id):
        get_object_or_404(eligible_parties(kind), pk=party_id)
        row = snapshot(
            actor=request.user, party_id=party_id, kind=kind, payload=request.data
        )
        return Response(StatementSerializer(row).data, status=201)


class StatementView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def checked(self, request, pk):
        require(request.user, "ledger.view_reconciliation")
        row = get_object_or_404(Reconciliation, pk=pk)
        require(
            request.user,
            "ledger.view_reconciliation",
            f"ledger.view_{row.ledger_kind}ledger",
        )
        return row

    def get(self, request, pk):
        row = self.checked(request, pk)
        return Response(
            {
                **StatementSerializer(row).data,
                "reviews": ReviewSerializer(
                    row.reviews.select_related("actor").order_by("created_at"),
                    many=True,
                ).data,
            }
        )

    def post(self, request, pk):
        row = review(
            actor=request.user,
            statement=self.checked(request, pk),
            payload=request.data,
        )
        return Response(ReviewSerializer(row).data, status=201)


class AccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerAccount
        fields = ["id", "name", "kind", "currency", "is_active"]


class AccountsView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        require(user)
        if not any(
            user.has_perm(f"ledger.{p}")
            for p in (
                "view_ledgeraccount",
                "view_cashbookentry",
                "add_cashbookentry",
                "view_salesledger",
                "view_purchaseledger",
            )
        ):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied()
        return Response(
            AccountSerializer(
                LedgerAccount.objects.order_by("currency", "kind", "name"), many=True
            ).data
        )

    def post(self, request):
        require(request.user, "ledger.view_ledgeraccount", "ledger.add_ledgeraccount")
        form = AccountSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        form.save()
        return Response(form.data, status=201)

    def patch(self, request):
        require(
            request.user, "ledger.view_ledgeraccount", "ledger.change_ledgeraccount"
        )
        row = get_object_or_404(
            LedgerAccount,
            pk=serializers.UUIDField().run_validation(request.data.get("id")),
        )
        if "currency" in request.data and request.data["currency"] != row.currency:
            raise serializers.ValidationError(
                "Account currency is fixed. Create a separate account."
            )
        if "kind" in request.data and request.data["kind"] != row.kind:
            raise serializers.ValidationError(
                "Account type is fixed. Create a separate account."
            )
        form = AccountSerializer(row, data=request.data, partial=True)
        form.is_valid(raise_exception=True)
        form.save()
        return Response(form.data)


class ProcurementInboxView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from procurement import models as p
        from procurement.services import visible_requests

        require(request.user, "ledger.approve_procurement")
        scope = visible_requests(request.user)
        rows = []
        versions = p.DocumentVersion.objects.filter(
            register__request__in=scope,
            approval_requested_at__isnull=False,
            quotation_revision__isnull=False,
        ).select_related("quotation_revision", "register")
        from .models import (
            ProcurementApproval,
            ProcurementLedgerLink,
            FinanceSubmission,
            FinanceApproval,
        )

        decided = ProcurementApproval.objects.values("revision_id")
        for version in versions.exclude(quotation_revision_id__in=decided):
            rows.append(
                {
                    "id": str(version.quotation_revision_id),
                    "kind": "QUOTATION",
                    "label": version.quotation_revision.number,
                    "request_id": str(version.register.request_id),
                }
            )
        from .models import SalesInvoice

        submitted = FinanceSubmission.objects.values("request_id")
        for source in (
            p.FinanceRequest.objects.filter(
                request__in=scope, pk__in=submitted, kind="INVOICE"
            )
            .exclude(
                pk__in=SalesInvoice.objects.exclude(finance_request_id=None).values(
                    "finance_request_id"
                )
            )
            .exclude(
                pk__in=ProcurementLedgerLink.objects.exclude(
                    finance_request_id=None
                ).values("finance_request_id")
            )
        ):
            rows.append(
                {
                    "id": str(source.pk),
                    "kind": "INVOICE",
                    "label": source.notes or "Invoice request",
                    "currency": source.currency,
                    "request_id": str(source.request_id),
                }
            )

        for source in (
            p.FinanceRequest.objects.filter(request__in=scope, pk__in=submitted)
            .exclude(kind="INVOICE")
            .exclude(pk__in=FinanceApproval.objects.values("request_id"))
        ):
            rows.append(
                {
                    "id": str(source.pk),
                    "kind": "REQUEST",
                    "label": f"{source.kind}: {source.notes}",
                    "currency": source.currency,
                    "request_id": str(source.request_id),
                }
            )
        for source in p.SupplierPurchase.objects.filter(request__in=scope).exclude(
            pk__in=ProcurementLedgerLink.objects.exclude(
                supplier_purchase_id=None
            ).values("supplier_purchase_id")
        ):
            rows.append(
                {
                    "id": str(source.pk),
                    "kind": "BILL",
                    "label": source.reference or source.supplier_name,
                    "currency": source.currency,
                    "request_id": str(source.request_id),
                }
            )
        return self.get_paginated_response(self.paginate_queryset(rows))

    def post(self, request):
        from .procurement_bridge import act

        return Response(act(request.user, request.data), status=201)


class TransactionChoicesView(GenericAPIView):
    permission_classes = [PartyLedgerPermission]

    def get(self, request, kind, party_id):
        from .models import PartyLedgerTransaction

        get_object_or_404(eligible_parties(kind), pk=party_id)
        currency = serializers.ChoiceField(
            choices=["NGN", "USD", "GBP"]
        ).run_validation(request.query_params.get("currency", "NGN"))
        rows = (
            PartyLedgerTransaction.objects.filter(
                party_id=party_id,
                ledger_kind=kind,
                currency=currency,
                reversal__isnull=True,
            )
            .order_by("-date", "-created_at")
            .values("id", "date", "transaction_type", "reference", "amount", "currency")
        )
        search = serializers.CharField(max_length=100, allow_blank=True).run_validation(
            request.query_params.get("search", "")
        )
        if search:
            from django.db.models import Q

            rows = rows.filter(
                Q(reference__icontains=search) | Q(description__icontains=search)
            )
        return self.get_paginated_response(self.paginate_queryset(rows))
