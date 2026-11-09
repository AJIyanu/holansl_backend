from datetime import date
from zoneinfo import ZoneInfo
from decimal import Decimal

from django.db.models import (
    Case,
    When,
    F,
    Q,
    Sum,
    Window,
    DecimalField,
    Exists,
    OuterRef,
)
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from .models import CashBookEntry, CashBookJournal, TaxEntry
from .serializers import (
    CashBookSerializer,
    CashBookWriteSerializer,
    CashBookEditSerializer,
    CashBookVoidSerializer,
    CashBookJournalSerializer,
)
from .services import create_entry, revise_entry


class CashBookPermission(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if (
            not user.is_authenticated
            or not user.is_active
            or not (user.is_staff or user.is_superuser)
        ):
            return False
        resource = (
            "cashbookjournal"
            if isinstance(view, CashBookJournalViewSet)
            else "cashbookentry"
        )
        action = {
            "POST": "add",
            "PUT": "change",
            "PATCH": "change",
            "DELETE": "delete",
        }.get(request.method, "view")
        return user.has_perm(f"ledger.view_{resource}") and user.has_perm(
            f"ledger.{action}_{resource}"
        )


def period(request):
    class PeriodSerializer(serializers.Serializer):
        year = serializers.IntegerField(
            min_value=1,
            max_value=9999,
            default=timezone.localdate(timezone=ZoneInfo("Africa/Lagos")).year,
        )
        date_from = serializers.DateField(required=False)
        date_to = serializers.DateField(required=False)

    params = PeriodSerializer(data=request.query_params)
    params.is_valid(raise_exception=True)
    values = params.validated_data
    year = values["year"]
    start = values.get("date_from", date(year, 1, 1))
    end = values.get("date_to", date(year, 12, 31))
    if start > end or start.year != year or end.year != year:
        raise serializers.ValidationError(
            "Choose a date range within the selected January–December financial year."
        )
    return year, start, end


def currency_filter(request, queryset, journal=False):
    currency = serializers.ChoiceField(choices=["NGN", "USD", "GBP"]).run_validation(
        request.query_params.get("currency", "NGN")
    )
    queryset = queryset.filter(currency=currency)
    account = request.query_params.get("account")
    if account:
        account = serializers.UUIDField().run_validation(account)
        queryset = queryset.filter(
            **{"entry__account_id" if journal else "account_id": account}
        )
    return queryset


def signed_amount():
    return Case(
        When(kind="EXPENSE", then=-F("amount")),
        default=F("amount"),
        output_field=DecimalField(max_digits=24, decimal_places=2),
    )


class CashBookViewSet(viewsets.ModelViewSet):
    queryset = CashBookEntry.objects.select_related(
        "created_by", "updated_by", "party_transaction", "staff_transaction"
    ).annotate(has_tax=Exists(TaxEntry.objects.filter(cash_entry_id=OuterRef("pk"))))
    serializer_class = CashBookSerializer
    permission_classes = [CashBookPermission]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def list(self, request, *args, **kwargs):
        year, start, end = period(request)
        active = currency_filter(
            request, self.get_queryset().filter(voided_at__isnull=True)
        )
        opening = active.filter(date__lt=start).aggregate(total=Sum(signed_amount()))[
            "total"
        ] or Decimal("0")
        selected = active.filter(date__range=(start, end))
        totals = selected.aggregate(
            debit=Sum("amount", filter=~Q(kind="EXPENSE")),
            credit=Sum("amount", filter=Q(kind="EXPENSE")),
        )
        debit, credit = (
            totals["debit"] or Decimal("0"),
            totals["credit"] or Decimal("0"),
        )
        selected = selected.annotate(
            running_balance=Window(
                Sum(signed_amount()),
                order_by=[F("date").asc(), F("created_at").asc(), F("id").asc()],
            )
            + opening
        )
        page = self.paginate_queryset(selected)
        response = self.get_paginated_response(
            self.get_serializer(page, many=True).data
        )
        response.data["summary"] = {
            "currency": request.query_params.get("currency", "NGN"),
            "year": year,
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "opening_balance": f"{opening:.2f}",
            "debit": f"{debit:.2f}",
            "credit": f"{credit:.2f}",
            "closing_balance": f"{opening + debit - credit:.2f}",
        }
        return response

    def create(self, request, *args, **kwargs):
        serializer = CashBookWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = create_entry(serializer.validated_data, request.user)
        return Response(self.get_serializer(entry).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        entry = self.get_object()
        serializer = CashBookEditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = revise_entry(entry.pk, serializer.validated_data, request.user)
        return Response(self.get_serializer(entry).data)

    def destroy(self, request, *args, **kwargs):
        entry = self.get_object()
        serializer = CashBookVoidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        revise_entry(entry.pk, serializer.validated_data, request.user, void=True)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CashBookJournalViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = CashBookJournal.objects.select_related("actor")
    serializer_class = CashBookJournalSerializer
    permission_classes = [CashBookPermission]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "list":
            _, start, end = period(self.request)
            queryset = currency_filter(
                self.request, queryset.filter(date__range=(start, end)), journal=True
            )
        return queryset
